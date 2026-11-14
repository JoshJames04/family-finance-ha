"""Refunds/reimbursements: a transaction may refund another transaction.

Rules: the refund must be a positive inflow, cannot refund a refund, and
cumulative refunds cannot exceed the original spend. Cross-account links
are allowed (spend on personal, reimbursed to joint). Reports expose both
gross and net-of-refunds views of category spend.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.domain.expected_matches import acyclic_transaction_ids
from app.domain.reporting import external_category_amounts
from app.persistence.models import Transaction, TransactionStatus


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
    """Count live refund families once, including equivalent legacy NULL links."""
    with session.no_autoflush:
        orig = session.get(Transaction, original_id)
        if orig is None:
            raise RefundError("original transaction not found")
        current, neighbor = aliased(Transaction), aliased(Transaction)
        family = select(Transaction.id.label("id")).where(
            Transaction.refund_of_id == original_id).cte(recursive=True)
        family = family.union(select(neighbor.id).select_from(family).join(
            current, current.id == family.c.id).join(neighbor, or_(
                neighbor.corrects_id == current.id, current.corrects_id == neighbor.id)))
        rows = {row.id: row for row in session.scalars(
            select(Transaction).where(Transaction.id.in_(select(family.c.id))))}
        if rows and set(session.scalars(acyclic_transaction_ids(list(rows)))) != set(rows):
            raise RefundError("refund correction history contains a cycle")
    components: dict[int, list[Transaction]] = {}
    for row in rows.values():
        root = row
        while root.corrects_id in rows:
            root = rows[root.corrects_id]
        components.setdefault(root.id, []).append(row)
    refunded = 0
    for component in components.values():
        live = [row for row in component if row.status != TransactionStatus.void]
        if len(live) > 1:
            raise RefundError("refund correction history has ambiguous live replacements")
        if not live:
            continue
        payment = anchor = live[0]
        # Explicit live links are authoritative, even after edits. Infer a lost
        # legacy link only through financially equivalent void predecessors.
        while anchor.refund_of_id is None:
            parent = rows.get(anchor.corrects_id) if anchor.corrects_id is not None else None
            if (parent is None or parent.status != TransactionStatus.void
                    or (parent.account_id, parent.date, parent.amount_pence, parent.transfer_id)
                    != (anchor.account_id, anchor.date, anchor.amount_pence, anchor.transfer_id)):
                raise RefundError("refund correction history cannot prove the purchase link")
            anchor = parent
        if anchor.refund_of_id == original_id:
            if payment.amount_pence <= 0:
                raise RefundError("refund payment must be a positive inflow")
            refunded += payment.amount_pence
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
    amounts = external_category_amounts(Transaction.date >= start, Transaction.date <= end)
    stmt = select(func.coalesce(func.sum(amounts.c.amount_pence), 0)).where(
        amounts.c.category_id == category_id,
        or_(amounts.c.amount_pence < 0, amounts.c.refund_of_id.is_not(None))
        if net else amounts.c.amount_pence < 0)
    return int(session.execute(stmt).scalar_one())
