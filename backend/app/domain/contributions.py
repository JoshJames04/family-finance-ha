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
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    MergeCandidate,
    MergeStatus,
    StatementBankIdentity,
    Transaction,
    TransactionSource,
    TransactionStatus,
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
                            tx: Transaction) -> None:
    """Validate new evidence before a match, category or schedule write."""
    from app.persistence.models import Account

    if tx.status == TransactionStatus.void:
        raise MatchError("void transactions cannot satisfy expected movements")
    if tx.account_id != movement.account_id:
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


def bill_post_ref(movement: ExpectedMovement) -> str:
    return f"rec:{movement.source_id}:{movement.original_expected_date or movement.expected_date}"


def editable_bill_posts(
    session: Session, movements: Collection[ExpectedMovement], account_id: int,
) -> dict[int, Transaction]:
    """Unprotected pending ledger entries belonging to these exact bill occurrences."""
    from app.domain.categorise import transaction_editable

    bills = {m.id: m for m in movements
             if m.source_type == ExpectedSource.commitment and m.account_id == account_id}
    refs = {mid: bill_post_ref(m) for mid, m in bills.items()}
    if not refs:
        return {}
    accepted_refs = {*refs.values(), *(f"{ref}:provisional" for ref in refs.values())}
    other = aliased(ExpectedMatch)
    rows = session.execute(select(ExpectedMatch, Transaction).join(
        Transaction, ExpectedMatch.transaction_id == Transaction.id).where(
            ExpectedMatch.movement_id.in_(refs), Transaction.account_id == account_id,
            Transaction.source == TransactionSource.system,
            Transaction.status == TransactionStatus.pending,
            Transaction.external_ref.in_(accepted_refs), Transaction.transfer_id.is_(None),
            Transaction.refund_of_id.is_(None),
            ~select(other.id).where(other.transaction_id == Transaction.id,
                                    other.id != ExpectedMatch.id).exists(),
            ~select(MergeCandidate.id).where(
                MergeCandidate.existing_txn_id == Transaction.id,
                MergeCandidate.status != MergeStatus.dismissed).exists(),
            ~select(StatementBankIdentity.transaction_id).where(
                StatementBankIdentity.transaction_id == Transaction.id).exists()))
    editable = {}
    for match, post in rows:
        ref = refs[match.movement_id]
        if (post.external_ref in (ref, f"{ref}:provisional")
                and match.matched_pence == abs(post.amount_pence)
                and bills[match.movement_id].amount_pence * post.amount_pence > 0
                and not post.splits and transaction_editable(session, post)):
            editable[match.movement_id] = post
    return editable


def pending_bill_posts(
    session: Session, movements: Collection[ExpectedMovement], payment: Transaction,
) -> dict[int, Transaction]:
    if (payment.source in (TransactionSource.system, TransactionSource.manual_verification)
            or payment.refund_of_id is not None or payment.transfer_id is not None):
        return {}
    posts = editable_bill_posts(session, movements, payment.account_id)
    return {mid: post for mid, post in posts.items()
            if post.amount_pence * payment.amount_pence > 0}


def payment_outstanding(movement: ExpectedMovement, matched: int,
                        provisional: Transaction | None) -> int:
    """Capacity for real payment evidence after releasing a provisional posting."""
    posted = abs(provisional.amount_pence) if provisional is not None else 0
    return max(0, abs(movement.amount_pence) - max(0, matched - posted))


def _link(session: Session, movement: ExpectedMovement,
          tx: Transaction, pence: int, manual: bool) -> ExpectedMatch:
    m = ExpectedMatch(movement_id=movement.id, transaction_id=tx.id,
                      matched_pence=pence, manual=manual)
    session.add(m)
    session.flush()
    return m


def resize_bill_post(session: Session, movement: ExpectedMovement,
                     provisional: Transaction, remaining: int) -> None:
    """Preserve the user-void reference; only automatic retirement releases it for a retry."""
    ref = bill_post_ref(movement)
    if remaining <= 0:
        provisional.status = TransactionStatus.void
        provisional.external_ref = f"{ref}:superseded:{provisional.id}"
        return
    provisional.external_ref = f"{ref}:provisional"
    provisional.amount_pence = remaining if provisional.amount_pence > 0 else -remaining
    link = session.scalars(select(ExpectedMatch).where(
        ExpectedMatch.movement_id == movement.id,
        ExpectedMatch.transaction_id == provisional.id)).one()
    link.matched_pence = remaining


def record_payment(
    session: Session, movement: ExpectedMovement, tx: Transaction, pence: int, *,
    manual: bool, provisional: Transaction | None, outstanding: int,
) -> ExpectedMatch:
    """Exchange the allocated portion of an editable posting for real evidence."""
    if provisional is not None:
        remainder = min(abs(provisional.amount_pence), outstanding) - pence
        resize_bill_post(session, movement, provisional, remainder)
    return _link(session, movement, tx, pence, manual=manual)


def confirm(session: Session, movement: ExpectedMovement,
            tx: Transaction, *,
            pence: int | None = None,
            today: date | None = None) -> ExpectedMatch:
    """Manual confirmation of a leg against an expected movement."""
    assert_payment_eligible(session, movement, tx)
    if tx.id in _used_legs(session, household_id=movement.household_id):
        raise MatchError(
            f"transaction {tx.id} already matched to a movement")
    provisional = pending_bill_posts(session, [movement], tx).get(movement.id)
    matched = status(session, movement, today).matched_pence
    need = payment_outstanding(movement, matched, provisional)
    amount = min(abs(tx.amount_pence), need) \
        if pence is None else pence
    if amount <= 0 or amount > abs(tx.amount_pence):
        raise MatchError("match amount must be within the leg amount")
    if amount > need:
        raise MatchError("match exceeds the outstanding expected amount")
    return record_payment(session, movement, tx, amount, manual=True,
                          provisional=provisional, outstanding=need)


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
    if (movement.source_type == ExpectedSource.commitment
            and movement.original_expected_date is None):
        movement.original_expected_date = movement.expected_date
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
