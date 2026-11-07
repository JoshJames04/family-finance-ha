"""Contribution matching (issue #87).

Expected transfer-rule movements carry due dates; real transfer legs
(transactions with a ``transfer_id``) are matched to them — automatically
by account + amount-sign + date window, or manually confirmed by a user.
Each match is an ``ExpectedMatch`` row; a leg may satisfy at most one
movement.

States per expected movement:

- ``satisfied`` — matched total ≥ expected amount
- ``partial`` — 0 < matched < expected
- ``overdue`` — unmatched and the due date has passed
- ``pending`` — unmatched and the due date is still ahead
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Transaction,
)

DEFAULT_WINDOW_DAYS = 7


class ContributionState(enum.StrEnum):
    satisfied = "satisfied"
    partial = "partial"
    overdue = "overdue"
    pending = "pending"


class MatchError(Exception):
    """Domain validation failure → API maps to 409."""


@dataclass(frozen=True)
class ContributionStatus:
    movement_id: int
    expected_pence: int
    matched_pence: int
    outstanding_pence: int
    state: ContributionState


def matched_total(session: Session, movement_id: int) -> int:
    rows = session.execute(
        select(ExpectedMatch.matched_pence).where(
            ExpectedMatch.movement_id == movement_id)).scalars().all()
    return sum(rows)


def status(session: Session, movement: ExpectedMovement,
           today: date | None = None) -> ContributionStatus:
    today = today or date.today()
    expected = abs(movement.amount_pence)
    matched = matched_total(session, movement.id)
    if matched >= expected:
        state = ContributionState.satisfied
    elif matched > 0:
        state = ContributionState.partial
    elif movement.expected_date < today:
        state = ContributionState.overdue
    else:
        state = ContributionState.pending
    return ContributionStatus(
        movement_id=movement.id, expected_pence=expected,
        matched_pence=matched,
        outstanding_pence=max(0, expected - matched), state=state)


def _used_legs(session: Session) -> set[int]:
    return set(session.execute(
        select(ExpectedMatch.transaction_id)).scalars().all())


def _link(session: Session, movement: ExpectedMovement,
          tx: Transaction, pence: int, manual: bool) -> ExpectedMatch:
    m = ExpectedMatch(movement_id=movement.id, transaction_id=tx.id,
                      matched_pence=pence, manual=manual)
    session.add(m)
    session.flush()
    return m


def confirm(session: Session, movement: ExpectedMovement,
            tx: Transaction, *,
            pence: int | None = None,
            today: date | None = None) -> ExpectedMatch:
    """Manual confirmation of a leg against an expected movement."""
    if tx.account_id != movement.account_id:
        raise MatchError(
            f"transaction {tx.id} is on account {tx.account_id}, "
            f"expected {movement.account_id}")
    if (tx.amount_pence < 0) != (movement.amount_pence < 0):
        raise MatchError("transaction sign does not match the movement")
    if tx.id in _used_legs(session):
        raise MatchError(
            f"transaction {tx.id} already matched to a movement")
    need = status(session, movement, today).outstanding_pence
    amount = min(abs(tx.amount_pence), need) \
        if pence is None else pence
    if amount <= 0 or amount > abs(tx.amount_pence):
        raise MatchError("match amount must be within the leg amount")
    if amount > need:
        raise MatchError("match exceeds the outstanding expected amount")
    return _link(session, movement, tx, amount, manual=True)


def auto_match(session: Session, household_id: int, *,
               window_days: int = DEFAULT_WINDOW_DAYS,
               today: date | None = None) -> int:
    """Greedy auto-match: each transfer_rule movement takes the nearest
    unused same-account, same-sign leg within ±window_days of its due
    date. Returns the number of match rows written. Idempotent — legs
    already linked are skipped."""
    today = today or date.today()
    movements = list(session.execute(
        select(ExpectedMovement).where(
            ExpectedMovement.household_id == household_id,
            ExpectedMovement.source_type ==
            ExpectedSource.transfer_rule,
            ExpectedMovement.account_id.is_not(None))
        .order_by(ExpectedMovement.expected_date)).scalars())
    used = _used_legs(session)
    written = 0
    for mv in movements:
        need = status(session, mv, today).outstanding_pence
        if need <= 0:
            continue
        window = timedelta(days=window_days)
        candidates = session.execute(
            select(Transaction).where(
                Transaction.account_id == mv.account_id,
                Transaction.transfer_id.is_not(None),
                Transaction.date >= mv.expected_date - window,
                Transaction.date <= mv.expected_date + window,
                Transaction.amount_pence > 0
                if mv.amount_pence > 0 else
                Transaction.amount_pence < 0)
            .order_by(Transaction.date)).scalars().all()
        for tx in sorted(
                candidates,
                key=lambda t: abs(
                    (t.date - mv.expected_date).days)):
            if tx.id in used or need <= 0:
                continue
            take = min(abs(tx.amount_pence), need)
            _link(session, mv, tx, take, manual=False)
            used.add(tx.id)
            need -= take
            written += 1
    return written


def contributions(session: Session, household_id: int, *,
                  start: date, end: date,
                  today: date | None = None) -> list[ContributionStatus]:
    """Status for every transfer-rule movement in the window."""
    movements = session.execute(
        select(ExpectedMovement).where(
            ExpectedMovement.household_id == household_id,
            ExpectedMovement.source_type ==
            ExpectedSource.transfer_rule,
            ExpectedMovement.expected_date >= start,
            ExpectedMovement.expected_date <= end,
            ExpectedMovement.dismissed_at.is_(None))
        .order_by(ExpectedMovement.expected_date)).scalars().all()
    return [status(session, m, today) for m in movements]


# ---------- outstanding obligations (issue #88) --------------------


@dataclass(frozen=True)
class OutstandingItem:
    movement_id: int
    account_id: int | None
    label: str
    due_date: date
    age_days: int
    outstanding_pence: int


@dataclass(frozen=True)
class Requirement:
    """Per-account: scheduled in-legs vs satisfied, by due date."""
    account_id: int
    due_date: date
    scheduled_pence: int
    satisfied_pence: int
    outstanding_pence: int


def outstanding(session: Session, household_id: int, *,
                today: date | None = None) -> list[OutstandingItem]:
    """Past-due, unmatched (or partially matched) expected movements —
    the "what's late" list the alert hook consumes. Dismissed
    movements are excluded."""
    today = today or date.today()
    rows = session.execute(
        select(ExpectedMovement).where(
            ExpectedMovement.household_id == household_id,
            ExpectedMovement.expected_date < today,
            ExpectedMovement.dismissed_at.is_(None))
        .order_by(ExpectedMovement.expected_date)).scalars().all()
    out: list[OutstandingItem] = []
    for mv in rows:
        s = status(session, mv, today)
        if s.state in (ContributionState.overdue,
                       ContributionState.partial):
            out.append(OutstandingItem(
                movement_id=mv.id, account_id=mv.account_id,
                label=mv.label, due_date=mv.expected_date,
                age_days=(today - mv.expected_date).days,
                outstanding_pence=s.outstanding_pence))
    return out


def requirement_totals(session: Session, household_id: int, *,
                       start: date, end: date,
                       today: date | None = None
                       ) -> list[Requirement]:
    """Per (account, due date): scheduled in-leg amounts vs satisfied —
    "the bills account needs £X by dd/MM". Reconciles:
    outstanding = scheduled − satisfied, per row."""
    rows = session.execute(
        select(ExpectedMovement).where(
            ExpectedMovement.household_id == household_id,
            ExpectedMovement.source_type ==
            ExpectedSource.transfer_rule,
            ExpectedMovement.amount_pence > 0,
            ExpectedMovement.account_id.is_not(None),
            ExpectedMovement.expected_date >= start,
            ExpectedMovement.expected_date <= end,
            ExpectedMovement.dismissed_at.is_(None))
        .order_by(ExpectedMovement.account_id,
                  ExpectedMovement.expected_date)).scalars().all()
    return [Requirement(
        account_id=mv.account_id if mv.account_id is not None else 0,
        due_date=mv.expected_date,
        scheduled_pence=mv.amount_pence,
        satisfied_pence=(s := status(session, mv, today)).matched_pence,
        outstanding_pence=s.outstanding_pence)
        for mv in rows]


def write_off(session: Session, movement: ExpectedMovement,
              *, when: datetime) -> ExpectedMovement:
    """Abandon an obligation: excluded from outstanding and
    requirement totals thereafter. Audited by the caller."""
    if movement.dismissed_at is not None:
        raise MatchError(
            f"expected movement {movement.id} already written off")
    movement.dismissed_at = when
    session.flush()
    return movement


def reschedule(session: Session, movement: ExpectedMovement,
               new_date: date, *, today: date | None = None
               ) -> ExpectedMovement:
    """Move a due date forward for an obligation the household will
    still honour. Past movements and dismissed ones cannot be moved."""
    today = today or date.today()
    if movement.dismissed_at is not None:
        raise MatchError(
            f"expected movement {movement.id} is written off")
    if new_date <= movement.expected_date:
        raise MatchError("new due date must be after the current one")
    movement.expected_date = new_date
    session.flush()
    return movement


# ---------- per-member contributions (issue #89) -------------------


@dataclass(frozen=True)
class MemberContribution:
    profile_id: int
    display_name: str
    contributed_pence: int


def member_contributions(session: Session, household_id: int, *,
                         start: date, end: date
                         ) -> list[MemberContribution]:
    """Per-member contribution totals for a period (issue #89).

    A contribution is a matched transfer IN-leg on an expected
    movement (satisfied portion counts, not the scheduled amount —
    period totals equal real matched transfers, no phantom
    contributions). The contributing member is the owner of the
    sibling leg's account.

    Scope rule: legs where both accounts have the same owner are
    private shuffling and never appear in shared views — including
    shared→shared household moves (both NULL).
    """
    from app.persistence.models import Account, UserProfile

    # matched legs on this household's movements in the window —
    # either movement leg may be matched, so pull both signs and find
    # the transfer's source leg by sign (negative = money out)
    rows = session.execute(
        select(ExpectedMatch, ExpectedMovement, Transaction)
        .join(ExpectedMovement,
              ExpectedMatch.movement_id == ExpectedMovement.id)
        .join(Transaction,
              ExpectedMatch.transaction_id == Transaction.id)
        .where(ExpectedMovement.household_id == household_id,
               ExpectedMovement.expected_date >= start,
               ExpectedMovement.expected_date <= end,
               Transaction.transfer_id.is_not(None))).all()

    members = session.execute(
        select(UserProfile).where(
            UserProfile.household_id == household_id,
            UserProfile.is_active.is_(True))
        .order_by(UserProfile.display_name)).scalars().all()
    if not rows:
        # zeroed rows for all active members — the shared view shows
        # everyone's standing
        return [MemberContribution(m.id, m.display_name, 0)
                for m in members]

    transfer_ids = {tx.transfer_id for _, _, tx in rows}
    legs = session.execute(
        select(Transaction).where(
            Transaction.transfer_id.in_(transfer_ids))).scalars().all()
    src_of: dict[int, Transaction] = {}
    dst_of: dict[int, Transaction] = {}
    for leg in legs:
        if leg.transfer_id is None:  # query already filters
            continue
        (src_of if leg.amount_pence < 0 else dst_of)[
            leg.transfer_id] = leg
    account_ids = {leg.account_id for leg in legs}
    accounts = {a.id: a for a in session.execute(
        select(Account).where(Account.id.in_(account_ids))).scalars()}

    # one transfer = one contribution: a rule emits in+out movements
    # and both may be matched — count the transfer once, preferring
    # the in-leg's matched amount
    by_transfer_matched: dict[int, int] = {}
    for match, _mv, tx in rows:
        if tx.transfer_id is None:  # query already filters — mypy guard
            continue
        cur = by_transfer_matched.get(tx.transfer_id, 0)
        by_transfer_matched[tx.transfer_id] = max(
            cur, match.matched_pence)

    totals: dict[int, int] = {}
    for tid, matched in by_transfer_matched.items():
        src_leg = src_of.get(tid)
        dst_leg = dst_of.get(tid)
        src = (accounts.get(src_leg.account_id)
               if src_leg is not None else None)
        dst = (accounts.get(dst_leg.account_id)
               if dst_leg is not None else None)
        if src is None or dst is None:
            continue
        # private: same owner both sides — excluded from shared views
        if src.owner_profile_id == dst.owner_profile_id:
            continue
        if src.owner_profile_id is None:
            continue  # household-shared source isn't a member
        totals[src.owner_profile_id] = (
            totals.get(src.owner_profile_id, 0) + matched)

    members = session.execute(
        select(UserProfile).where(
            UserProfile.household_id == household_id,
            UserProfile.is_active.is_(True))
        .order_by(UserProfile.display_name)).scalars().all()
    return [MemberContribution(m.id, m.display_name,
                               totals.get(m.id, 0))
            for m in members]
