"""Seasonal events + gifts (issue #96).

Gift lifecycle is a guarded state machine — you can't wrap what you
haven't bought, and payment follows purchase. Totals derive from the
gift rows so the event view and the ledger can't disagree.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    Gift,
    PaymentStatus,
    PurchaseStatus,
    SeasonalEvent,
    Transaction,
)


class SeasonalError(Exception):
    """Domain validation failure → API maps to 409."""


# Forward-only purchase lifecycle; giving closes the chain.
PURCHASE_ORDER = {
    PurchaseStatus.idea: PurchaseStatus.planned,
    PurchaseStatus.planned: PurchaseStatus.purchased,
    PurchaseStatus.purchased: PurchaseStatus.wrapped,
    PurchaseStatus.wrapped: PurchaseStatus.given,
}


def transition_purchase(gift: Gift, to: PurchaseStatus) -> Gift:
    """Advance purchase state one step — skips and backwards moves
    are domain errors, never silent state corruption."""
    nxt = PURCHASE_ORDER.get(gift.purchase_status)
    if nxt is None:
        raise SeasonalError(f"gift already final ({gift.purchase_status})")
    if to is not nxt:
        raise SeasonalError(
            f"invalid transition {gift.purchase_status} → {to}; "
            f"next step is {nxt}")
    gift.purchase_status = to
    return gift


def mark_paid(session: Session, gift: Gift,
              tx: Transaction | None = None) -> Gift:
    """Record payment — requires purchased (or beyond) and an actual
    cost; optionally binds the real transaction it paid through."""
    if gift.purchase_status in (PurchaseStatus.idea,
                                PurchaseStatus.planned):
        raise SeasonalError("cannot pay for a gift not yet purchased")
    if gift.actual_pence is None:
        raise SeasonalError("actual cost required before payment")
    if tx is not None and tx.account.household_id \
            != gift.event.household_id:
        raise SeasonalError("transaction belongs to another household")
    gift.transaction_id = tx.id if tx is not None else gift.transaction_id
    gift.payment_status = PaymentStatus.paid
    session.flush()
    return gift


@dataclass(frozen=True)
class EventTotals:
    planned_pence: int
    actual_pence: int
    committed_pence: int  # actual where spent, else planned — real ask
    remaining_pence: int  # budget − committed (negative = over)


def totals(session: Session, event: SeasonalEvent) -> EventTotals:
    """Budget position for the event: committed uses the better-known
    figure per gift — actual once spent, else the plan."""
    rows = session.execute(
        select(
            func.coalesce(func.sum(Gift.planned_pence), 0),
            func.coalesce(func.sum(Gift.actual_pence), 0),
            func.coalesce(
                func.sum(func.coalesce(Gift.actual_pence,
                                       Gift.planned_pence)),
                0),
        ).where(Gift.event_id == event.id)).one()
    return EventTotals(
        planned_pence=int(rows[0] or 0),
        actual_pence=int(rows[1] or 0),
        committed_pence=int(rows[2] or 0),
        remaining_pence=event.budget_pence - int(rows[2] or 0))
