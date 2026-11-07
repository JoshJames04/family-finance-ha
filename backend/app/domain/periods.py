"""Reporting periods + reconciliation mutation paths.

`assert_editable` is the single gate every transaction mutation must pass:
locked period → PeriodLockedError; reconciled row → must correct, not edit.
`transition` applies status moves through the lifecycle graph.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.transactions import assert_transition
from app.persistence.models import (
    Account,
    ReportingPeriod,
    Transaction,
    TransactionStatus,
)


class PeriodLockedError(RuntimeError):
    pass


class ReconciledImmutableError(RuntimeError):
    pass


def open_period(
    session: Session,
    household_id: int,
    label: str,
    start: date,
    end: date,
) -> ReportingPeriod:
    p = ReportingPeriod(household_id=household_id, label=label,
                        start_date=start, end_date=end)
    session.add(p)
    session.flush()
    return p


def period_for(
    session: Session, household_id: int, d: date
) -> ReportingPeriod | None:
    return session.execute(
        select(ReportingPeriod).where(
            ReportingPeriod.household_id == household_id,
            ReportingPeriod.start_date <= d,
            ReportingPeriod.end_date >= d,
        )
    ).scalar_one_or_none()


def lock_period(session: Session, period: ReportingPeriod) -> None:
    """Lock a period: mark all its cleared transactions reconciled."""
    session.execute(
        update(Transaction)
        .where(
            Transaction.date >= period.start_date,
            Transaction.date <= period.end_date,
            Transaction.status == TransactionStatus.cleared,
            Transaction.account_id.in_(
                select(Account.id).where(
                    Account.household_id == period.household_id)
            ),
        )
        .values(status=TransactionStatus.reconciled)
    )
    period.locked = True
    session.flush()


def assert_editable(session: Session, txn: Transaction) -> None:
    """Gate for any mutation to an existing transaction row."""
    if txn.status == TransactionStatus.reconciled:
        raise ReconciledImmutableError(
            "reconciled transactions can only change via a correction"
        )
    account = session.get(Account, txn.account_id)
    assert account is not None
    p = period_for(session, account.household_id, txn.date)
    if p is not None and p.locked:
        raise PeriodLockedError(
            f"{txn.date} falls inside locked period {p.label!r}"
        )


def transition(
    session: Session,
    txn: Transaction,
    to: TransactionStatus,
) -> None:
    """Apply a lifecycle transition through the domain graph.

    Reconciled→anything is refused by the graph; a move INTO a locked
    period's window is also refused unless the target is `reconciled`
    as part of the lock itself (handled via lock_period's bulk update).
    """
    assert_transition(txn.status, to)
    account = session.get(Account, txn.account_id)
    assert account is not None
    p = period_for(session, account.household_id, txn.date)
    if p is not None and p.locked:
        raise PeriodLockedError(
            f"{txn.date} falls inside locked period {p.label!r}"
        )
    txn.status = to
    session.flush()
