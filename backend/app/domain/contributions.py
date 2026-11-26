"""Contribution matching (issue #87).

Expected transfer-rule movements carry due dates; real transfer legs
(transactions with a ``transfer_id``) are matched to them — automatically
by transfer endpoints, amount sign and date window, or manually confirmed by a user.
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
import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.domain.accounts import current_movement_account
from app.domain.expected_matches import (
    SALARY_SOURCES,
    acyclic_transaction_ids,
    eligible_matches,
)
from app.persistence.models import (
    Account,
    AppMeta,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Transaction,
    TransactionStatus,
    TransferRule,
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
    return matched_totals(session, [movement_id])[movement_id]


def matched_totals(session: Session, movement_ids: Collection[int]) -> dict[int, int]:
    """One grouped read, including zeroes; aliases share live-payment capacity.

    Filter after allocation so requesting a subset cannot reuse capacity already
    claimed by another movement's older historical link.
    """
    totals = dict.fromkeys(movement_ids, 0)
    if totals:
        matches = eligible_matches(movement_ids)
        totals.update({mid: int(amount) for mid, amount in session.execute(select(
            matches.c.movement_id, func.sum(matches.c.matched_pence)).where(
            matches.c.movement_id.in_(totals)).group_by(matches.c.movement_id))})
    return totals


def shared_payment_totals(
    session: Session,
    incoming: list[ExpectedMovement],
    outgoing: dict[int, ExpectedMovement],
) -> dict[int, int]:
    """Credit a paired receipt once even if both scheduled legs were matched."""
    received = {movement.id: movement for movement in incoming}
    amounts: dict[int, dict[int, int]] = {mid: {} for mid in received}
    if not received:
        return {}
    evidence = eligible_matches([*received, *outgoing])
    rows = session.execute(
        select(evidence.c.movement_id, evidence.c.matched_pence, Transaction)
        .select_from(evidence).join(Transaction, Transaction.id == evidence.c.transaction_id)
        .where(evidence.c.movement_id.in_([*received, *outgoing]))
    ).all()
    transfer_ids = {tx.transfer_id for mid, _, tx in rows
                    if mid in outgoing and tx.transfer_id is not None}
    credits: dict[tuple[int, int], list[Transaction]] = {}
    if transfer_ids:
        for credit in session.scalars(select(Transaction).where(
                Transaction.transfer_id.in_(transfer_ids),
                Transaction.status != TransactionStatus.void, Transaction.amount_pence > 0)):
            assert credit.transfer_id is not None
            credits.setdefault((credit.transfer_id, credit.account_id), []).append(credit)
    direct_receipts = {tx.id: mid for mid, _, tx in rows if mid in received}
    credited = dict(direct_receipts)
    for mid, pence, tx in rows:
        target = outgoing.get(mid)
        if target is not None:
            candidates = (credits.get((tx.transfer_id, target.account_id), [])
                          if tx.transfer_id is not None and target.account_id is not None else [])
            if len(candidates) != 1:
                continue
            tx = candidates[0]
            mid = target.id
            if credited.setdefault(tx.id, mid) != mid:
                continue
        payment = amounts[mid]
        payment[tx.id] = max(payment.get(tx.id, 0), min(pence, tx.amount_pence))
    return {mid: min(received[mid].amount_pence, sum(payments.values()))
            for mid, payments in amounts.items()}


def status(session: Session, movement: ExpectedMovement,
           today: date | None = None) -> ContributionStatus:
    return _status(movement, matched_total(session, movement.id), today)


def _status(movement: ExpectedMovement, matched: int,
            today: date | None = None) -> ContributionStatus:
    today = today or date.today()
    expected = abs(movement.amount_pence)
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


def _used_legs(session: Session, *, household_id: int | None = None) -> set[int]:
    matches = eligible_matches(select(ExpectedMovement.id).where(
        ExpectedMovement.household_id == household_id)) if household_id is not None \
        else eligible_matches()
    return set(session.scalars(select(matches.c.transaction_id)))


def assert_payment_eligible(session: Session, movement: ExpectedMovement,
                            tx: Transaction, *, alternate_destination: bool = False) -> None:
    """Validate new evidence before a match, category or schedule write."""
    if tx.status == TransactionStatus.void:
        raise MatchError("void transactions cannot satisfy expected movements")
    if tx.account_id != movement.account_id and not alternate_destination:
        raise MatchError("transaction account does not match the movement")
    if tx.amount_pence * movement.amount_pence <= 0:
        raise MatchError("transaction must be nonzero and match the movement sign")
    if (movement.source_type in SALARY_SOURCES
            and tx.refund_of_id is not None):
        raise MatchError("a purchase refund cannot satisfy a salary expectation")
    if session.scalar(select(Account.household_id).where(
            Account.id == tx.account_id)) != movement.household_id:
        raise MatchError("transaction household does not match the movement")
    if session.scalar(select(ExpectedMovement.id).where(
        ExpectedMovement.id == movement.id,
        ExpectedMovement.dismissed_at.is_(None),
        current_movement_account(),
    )) is None:
        raise MatchError("expected movement is no longer available")
    if session.scalar(acyclic_transaction_ids([tx.id]).limit(1)) is None:
        raise MatchError("cyclic transaction corrections cannot satisfy expected movements")


def _link(session: Session, movement: ExpectedMovement,
          tx: Transaction, pence: int, manual: bool, *,
          alternate_destination: bool = False) -> ExpectedMatch:
    m = ExpectedMatch(movement_id=movement.id, transaction_id=tx.id,
                      matched_pence=pence, manual=manual,
                      alternate_destination=alternate_destination)
    session.add(m)
    session.flush()
    return m


def _direct_contribution_debit(
    session: Session, movement: ExpectedMovement, receipt: Transaction,
) -> tuple[ExpectedMovement, Transaction]:
    if (movement.source_type != ExpectedSource.transfer_rule or movement.amount_pence <= 0
            or receipt.transfer_id is None or movement.account_id is None):
        raise MatchError("choose a matched bank transfer into an eligible shared account")
    rule = session.get(TransferRule, movement.source_id)
    if rule is None or rule.household_id != movement.household_id:
        raise MatchError("the contribution schedule is unavailable")
    source_id = movement.transfer_source_account_id or rule.source_account_id
    destination_id = movement.transfer_dest_account_id or rule.dest_account_id
    source = session.get(Account, source_id)
    destination = session.get(Account, destination_id)
    alternate = session.get(Account, receipt.account_id)
    settings = session.get(AppMeta, f"shared_split:{movement.household_id}:{destination_id}")
    allowed = json.loads(settings.value).get("alternate_account_ids") or [] if settings else []
    if (source is None or source.owner_profile_id is None or destination is None
            or destination.owner_profile_id is not None or movement.account_id != destination_id
            or alternate is None or alternate.owner_profile_id is not None or alternate.archived
            or alternate.id not in allowed):
        raise MatchError("the receiving account is outside this contribution's setup")
    legs = session.scalars(select(Transaction).where(
        Transaction.transfer_id == receipt.transfer_id,
        Transaction.status != TransactionStatus.void,
    )).all()
    debit_rows = [leg for leg in legs if leg.account_id == source_id
                  and leg.amount_pence == -receipt.amount_pence]
    debit_plan = session.scalar(select(ExpectedMovement).where(
        ExpectedMovement.source_type == ExpectedSource.transfer_rule,
        ExpectedMovement.source_id == rule.id,
        ExpectedMovement.household_id == movement.household_id,
        ExpectedMovement.expected_date == movement.expected_date,
        ExpectedMovement.account_id == source_id,
        ExpectedMovement.amount_pence < 0,
        ExpectedMovement.dismissed_at.is_(None),
    ))
    if debit_plan is None or len(legs) != 2 or len(debit_rows) != 1:
        raise MatchError("the transfer must have one matching leg from the contributor's account")
    return debit_plan, debit_rows[0]


def confirm(session: Session, movement: ExpectedMovement,
            tx: Transaction, *,
            pence: int | None = None,
            today: date | None = None) -> ExpectedMatch:
    """Manual confirmation of a leg against an expected movement."""
    alternate = tx.account_id != movement.account_id
    assert_payment_eligible(session, movement, tx, alternate_destination=alternate)
    paired = _direct_contribution_debit(session, movement, tx) if alternate else None
    used = _used_legs(session, household_id=movement.household_id)
    if tx.id in used or (paired is not None and paired[1].id in used):
        raise MatchError(f"transaction {tx.id} already matched to a movement")
    need = status(session, movement, today).outstanding_pence
    if paired is not None:
        assert_payment_eligible(session, paired[0], paired[1])
        need = min(need, status(session, paired[0], today).outstanding_pence)
    amount = min(abs(tx.amount_pence), need) if pence is None else pence
    if amount <= 0 or amount > abs(tx.amount_pence):
        raise MatchError("match amount must be within the leg amount")
    if amount > need:
        raise MatchError("match exceeds the outstanding expected amount")
    if paired is not None:
        _link(session, paired[0], paired[1], amount, manual=True)
    return _link(session, movement, tx, amount, manual=True, alternate_destination=alternate)



def auto_match(session: Session, household_id: int, *,
               window_days: int = DEFAULT_WINDOW_DAYS,
               account_ids: set[int] | None = None,
               today: date | None = None) -> int:
    """Greedy auto-match: each transfer_rule movement takes the nearest
    unused same-account, same-sign leg within ±window_days of its due
    date. Returns the number of match rows written. Idempotent — legs
    already linked are skipped."""
    today = today or date.today()
    movements = list(session.execute(
        select(ExpectedMovement).where(
            current_movement_account(),
            ExpectedMovement.dismissed_at.is_(None),
            ExpectedMovement.household_id == household_id,
            ExpectedMovement.source_type ==
            ExpectedSource.transfer_rule,
            ExpectedMovement.account_id.is_not(None))
        .order_by(ExpectedMovement.expected_date)).scalars())
    used = _used_legs(session, household_id=household_id)
    totals = matched_totals(session, [mv.id for mv in movements])
    rules = {rule.id: rule for rule in session.scalars(select(TransferRule).where(
        TransferRule.household_id == household_id,
        TransferRule.id.in_({movement.source_id for movement in movements}),
    ))}
    configured = {int(key.rsplit(":", 1)[1]) for key in session.scalars(select(AppMeta.key).where(
        AppMeta.key.like(f"shared_split:{household_id}:%"),
    ))}
    counterpart = aliased(Transaction)

    written = 0
    for mv in movements:
        if account_ids is not None and mv.account_id not in account_ids:
            continue
        need = max(0, abs(mv.amount_pence) - totals[mv.id])
        if need <= 0:
            continue
        window = timedelta(days=window_days)
        candidates_query = (select(Transaction).where(
                Transaction.account_id == mv.account_id,
                Transaction.status != TransactionStatus.void,
                Transaction.transfer_id.is_not(None),
                Transaction.date >= mv.expected_date - window,
                Transaction.date <= mv.expected_date + window,
                Transaction.amount_pence > 0
                if mv.amount_pence > 0 else
                Transaction.amount_pence < 0)
            .order_by(Transaction.date))
        rule = rules.get(mv.source_id)
        source = mv.transfer_source_account_id or (rule.source_account_id if rule else None)
        destination = mv.transfer_dest_account_id or (rule.dest_account_id if rule else None)
        expected_account = source if mv.amount_pence > 0 else destination
        if expected_account is not None:
            other_legs = select(counterpart.id).where(
                counterpart.transfer_id == Transaction.transfer_id,
                counterpart.id != Transaction.id,
            ).correlate(Transaction)
            correct_leg = other_legs.where(
                counterpart.account_id == expected_account,
                counterpart.status != TransactionStatus.void,
                counterpart.amount_pence == -Transaction.amount_pence,
            ).exists()
            # Old ledger data can contain a single transfer leg. Preserve its existing
            # matching behavior unless the household has configured this contribution pot.
            if destination in configured:
                candidates_query = candidates_query.where(correct_leg)
            else:
                candidates_query = candidates_query.where(correct_leg | ~other_legs.exists())
        candidates = session.scalars(candidates_query.where(Transaction.id.in_(
            acyclic_transaction_ids(candidates_query.with_only_columns(
                Transaction.id).order_by(None))))).all()
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
            current_movement_account(),
            ExpectedMovement.household_id == household_id,
            ExpectedMovement.source_type ==
            ExpectedSource.transfer_rule,
            ExpectedMovement.expected_date >= start,
            ExpectedMovement.expected_date <= end,
            ExpectedMovement.dismissed_at.is_(None))
        .order_by(ExpectedMovement.expected_date)).scalars().all()
    totals = matched_totals(session, [m.id for m in movements])
    return [_status(m, totals[m.id], today) for m in movements]


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
            current_movement_account(),
            ExpectedMovement.household_id == household_id,
            ExpectedMovement.expected_date < today,
            ExpectedMovement.dismissed_at.is_(None))
        .order_by(ExpectedMovement.expected_date)).scalars().all()
    out: list[OutstandingItem] = []
    totals = matched_totals(session, [mv.id for mv in rows])
    for mv in rows:
        s = _status(mv, totals[mv.id], today)
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
            current_movement_account(),
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
    totals = matched_totals(session, [mv.id for mv in rows])
    return [Requirement(
        account_id=mv.account_id if mv.account_id is not None else 0,
        due_date=mv.expected_date,
        scheduled_pence=mv.amount_pence,
        satisfied_pence=(s := _status(mv, totals[mv.id], today)).matched_pence,
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
    matches = eligible_matches(select(ExpectedMovement.id).where(
        ExpectedMovement.household_id == household_id))
    rows = session.execute(
        select(matches.c.matched_pence, ExpectedMovement, Transaction).select_from(matches)
        .join(ExpectedMovement,
              matches.c.movement_id == ExpectedMovement.id)
        .join(Transaction,
              matches.c.transaction_id == Transaction.id)
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
            Transaction.transfer_id.in_(transfer_ids),
            Transaction.status != TransactionStatus.void,
            Transaction.amount_pence != 0)).scalars().all()
    src_of: dict[int, Transaction] = {}
    dst_of: dict[int, Transaction] = {}
    leg_counts: dict[int, int] = {}
    for leg in legs:
        if leg.transfer_id is None:  # query already filters
            continue
        leg_counts[leg.transfer_id] = leg_counts.get(leg.transfer_id, 0) + 1
        (src_of if leg.amount_pence < 0 else dst_of)[
            leg.transfer_id] = leg
    account_ids = {leg.account_id for leg in legs}
    accounts = {a.id: a for a in session.execute(
        select(Account).where(Account.id.in_(account_ids))).scalars()}

    # one transfer = one contribution: a rule emits in+out movements
    # and both may be matched — count the transfer once, preferring
    # the in-leg's matched amount
    by_transfer_matched: dict[int, int] = {}
    for matched_pence, _mv, tx in rows:
        if tx.transfer_id is None:  # query already filters — mypy guard
            continue
        cur = by_transfer_matched.get(tx.transfer_id, 0)
        by_transfer_matched[tx.transfer_id] = max(
            cur, matched_pence)

    totals: dict[int, int] = {}
    for tid, matched in by_transfer_matched.items():
        if leg_counts.get(tid) != 2:
            continue  # missing/ambiguous live counterparts cannot prove attribution
        src_leg = src_of.get(tid)
        dst_leg = dst_of.get(tid)
        src = (accounts.get(src_leg.account_id)
               if src_leg is not None else None)
        dst = (accounts.get(dst_leg.account_id)
               if dst_leg is not None else None)
        if src is None or dst is None:
            continue
        if src.household_id != household_id or dst.household_id != household_id:
            continue
        # private: same owner both sides — excluded from shared views
        if src.owner_profile_id == dst.owner_profile_id:
            continue
        if src.owner_profile_id is None:
            continue  # household-shared source isn't a member
        assert src_leg is not None and dst_leg is not None
        totals[src.owner_profile_id] = (totals.get(src.owner_profile_id, 0)
            + min(matched, abs(src_leg.amount_pence), abs(dst_leg.amount_pence)))

    members = session.execute(
        select(UserProfile).where(
            UserProfile.household_id == household_id,
            UserProfile.is_active.is_(True))
        .order_by(UserProfile.display_name)).scalars().all()
    return [MemberContribution(m.id, m.display_name,
                               totals.get(m.id, 0))
            for m in members]
