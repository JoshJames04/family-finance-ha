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
from datetime import date, timedelta

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
            ExpectedMovement.expected_date <= end)
        .order_by(ExpectedMovement.expected_date)).scalars().all()
    return [status(session, m, today) for m in movements]
