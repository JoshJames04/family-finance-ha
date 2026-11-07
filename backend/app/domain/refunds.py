"""Refunds/reimbursements: a transaction may refund another transaction.

Rules: the refund must be a positive inflow, cannot refund a refund, and
cumulative refunds cannot exceed the original spend. Cross-account links
are allowed (spend on personal, reimbursed to joint). Reports expose both
gross and net-of-refunds views of category spend.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import Transaction


class RefundError(ValueError):
    pass


def link_refund(
    session: Session,
    refund: Transaction,
    original: Transaction,
) -> None:
    """Mark `refund` as refunding `original`.

    Partial refunds accumulate; total refunds may not exceed the original
    outflow. Mutates only `refund` — originals are never edited.
    """
    if original.amount_pence >= 0:
        # covers "refund a refund" too — refunds are always positive
        raise RefundError("can only refund an outflow (negative) row")
    if refund.amount_pence <= 0:
        raise RefundError("refund must be a positive inflow")
    if refund.refund_of_id is not None:
        raise RefundError("refund is already linked")
    remaining = refundable_balance(session, original.id)
    if refund.amount_pence > remaining:
        raise RefundError(
            f"refund {refund.amount_pence} exceeds remaining "
            f"{remaining}"
        )
    refund.refund_of_id = original.id
    session.flush()


def refundable_balance(session: Session, original_id: int) -> int:
    """How much of `original` is still refundable (pence, >= 0)."""
    orig = session.get(Transaction, original_id)
    if orig is None:
        raise RefundError("original transaction not found")
    refunded = session.execute(
        select(func.coalesce(func.sum(Transaction.amount_pence), 0))
        .where(Transaction.refund_of_id == original_id)
    ).scalar_one()
    return -orig.amount_pence - refunded


def category_spend(
    session: Session,
    category_id: int,
    start: date,
    end: date,
    *,
    net: bool = True,
) -> int:
    """Spend in a category for [start, end], in pence (negative = spend).

    net=True  — refunds reduce spend (refund-of rows are included).
    net=False — gross spend only (refund-of rows excluded).
    """
    stmt = (
        select(func.coalesce(func.sum(Transaction.amount_pence), 0))
        .where(
            Transaction.category_id == category_id,
            Transaction.date >= start,
            Transaction.date <= end,
            Transaction.transfer_id.is_(None),
        )
    )
    if not net:
        stmt = stmt.where(Transaction.refund_of_id.is_(None))
    return int(session.execute(stmt).scalar_one())
