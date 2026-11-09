"""Transfer rules — two legs, one debit + one credit, sum zero."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import Transaction, Transfer


class TransferError(ValueError):
    pass


def create_transfer(
    session: Session,
    household_id: int,
    debit: Transaction,
    credit: Transaction,
    *,
    matched: bool = True,
    rule_ref: str | None = None,
    notes: str | None = None,
) -> Transfer:
    """Link two transaction legs into a transfer.

    Enforces: different accounts, opposite signs, equal magnitude
    (sum = 0), neither leg already part of a transfer.
    """
    if debit.account_id == credit.account_id:
        raise TransferError("legs must be on different accounts")
    if debit.amount_pence >= 0:
        raise TransferError("debit leg must be negative (money out)")
    if credit.amount_pence <= 0:
        raise TransferError("credit leg must be positive (money in)")
    if debit.amount_pence + credit.amount_pence != 0:
        raise TransferError("legs must sum to zero")
    if debit.transfer_id is not None or credit.transfer_id is not None:
        raise TransferError("a leg is already part of a transfer")

    t = Transfer(household_id=household_id, matched=matched,
                 rule_ref=rule_ref, notes=notes)
    session.add(t)
    session.flush()
    debit.transfer_id = credit.transfer_id = t.id
    session.flush()
    return t


def legs_balance(session: Session, transfer_id: int) -> int:
    """Integrity check: sum of leg amounts — must always be 0."""
    rows = session.execute(
        select(Transaction.amount_pence).where(
            Transaction.transfer_id == transfer_id)
    ).scalars().all()
    return sum(rows)
