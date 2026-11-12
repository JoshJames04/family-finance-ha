"""Seasonal events + gifts (issue #96).

Gift lifecycle is a guarded state machine — you can't wrap what you
haven't bought, and payment follows purchase. Totals derive from the
gift rows so the event view and the ledger can't disagree.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, object_session

from app.domain import outbox
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


def _emit_gift(session: Session, gift: Gift, transition: str) -> None:
    outbox.emit(
        session, gift.event.household_id, f"gift.{transition}",
        entity_kind="gift", entity_id=gift.id,
        payload={"source": "seasonal", "status": transition,
                 "owner_profile_id": gift.event.owner_profile_id},
        dedup_key=f"gift:{gift.id}:{transition}")


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
    if to is PurchaseStatus.purchased and (session := object_session(gift)) is not None:
        session.flush()
        _emit_gift(session, gift, "purchased")
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
    _emit_gift(session, gift, "paid")
    return gift


@dataclass(frozen=True)
class EventTotals:
    planned_pence: int
    actual_pence: int
    committed_pence: int  # actual where spent, else planned — real ask
    remaining_pence: int  # budget minus recorded actual spend
    remaining_after_commitments_pence: int


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
        remaining_pence=event.budget_pence - int(rows[1] or 0),
        remaining_after_commitments_pence=event.budget_pence - int(rows[2] or 0))


@dataclass(frozen=True)
class RecipientComparison:
    recipient: str
    earlier_actual_pence: int
    later_actual_pence: int
    delta_pence: int
    earlier_gifts: int
    later_gifts: int


@dataclass(frozen=True)
class EventComparison:
    """Year-on-year view of two events (issue #99): totals, per-
    recipient drift, and average actual spend per priced gift."""
    earlier: EventTotals
    later: EventTotals
    actual_delta_pence: int
    earlier_avg_gift_pence: int | None
    later_avg_gift_pence: int | None
    recipients: tuple[RecipientComparison, ...]


def _avg_gift(session: Session, event: SeasonalEvent) -> int | None:
    n, total = session.execute(
        select(func.count(), func.coalesce(func.sum(Gift.actual_pence), 0))
        .where(Gift.event_id == event.id, Gift.actual_pence.is_not(None))
    ).one()
    return int(int(total or 0) // n) if n else None


def compare(
    session: Session, earlier: SeasonalEvent, later: SeasonalEvent,
) -> EventComparison:
    """Compare two events of any type — drift is per-recipient so the
    report survives recipient changes between years."""
    if earlier.household_id != later.household_id:
        raise SeasonalError("events belong to different households")
    if earlier.year >= later.year:
        raise SeasonalError("earlier event must predate the later one")
    rows = session.execute(
        select(
            Gift.recipient,
            func.sum(case((Gift.event_id == earlier.id,
                           func.coalesce(Gift.actual_pence, 0)),
                          else_=0)),
            func.sum(case((Gift.event_id == later.id,
                           func.coalesce(Gift.actual_pence, 0)),
                          else_=0)),
            func.sum(case((Gift.event_id == earlier.id, 1), else_=0)),
            func.sum(case((Gift.event_id == later.id, 1), else_=0)),
        ).where(Gift.event_id.in_([earlier.id, later.id]))
        .group_by(Gift.recipient).order_by(Gift.recipient)).all()
    recipients = tuple(
        RecipientComparison(
            recipient=r[0], earlier_actual_pence=int(r[1] or 0),
            later_actual_pence=int(r[2] or 0),
            delta_pence=int(r[2] or 0) - int(r[1] or 0),
            earlier_gifts=int(r[3] or 0), later_gifts=int(r[4] or 0))
        for r in rows)
    et, lt = totals(session, earlier), totals(session, later)
    return EventComparison(
        earlier=et, later=lt,
        actual_delta_pence=lt.actual_pence - et.actual_pence,
        earlier_avg_gift_pence=_avg_gift(session, earlier),
        later_avg_gift_pence=_avg_gift(session, later),
        recipients=recipients)


def rollover(
    session: Session,
    event: SeasonalEvent,
    year: int,
    *,
    copy_planned: bool = False,
    carry_remaining: bool = False,
) -> SeasonalEvent:
    """Seed next year's event from this one (issue #98).

    Recipients + ideas carry over as fresh ``idea``-status gifts; last
    year's amounts do NOT carry by default — ``copy_planned`` opts in.
    ``carry_remaining`` folds unspent budget into the new event's
    budget (remaining-budget policy); otherwise the budget resets to
    the same figure and any surplus stays in the sinking fund.
    """
    clash = session.execute(
        select(SeasonalEvent).where(
            SeasonalEvent.household_id == event.household_id,
            SeasonalEvent.owner_profile_id == event.owner_profile_id,
            SeasonalEvent.type == event.type,
            SeasonalEvent.year == year)
    ).scalar_one_or_none()
    if clash is not None:
        raise SeasonalError(
            f"{event.type} {year} already exists (id={clash.id})")
    budget = event.budget_pence
    if carry_remaining:
        budget += max(0, totals(session, event).remaining_pence)
    new = SeasonalEvent(
        household_id=event.household_id, year=year, type=event.type,
        name=event.name, budget_pence=budget,
        savings_goal_id=event.savings_goal_id, notes=event.notes,
        owner_profile_id=event.owner_profile_id)
    session.add(new)
    session.flush()
    for g in event.gifts:
        session.add(Gift(
            event_id=new.id, recipient=g.recipient, idea=g.idea,
            planned_pence=g.planned_pence if copy_planned else 0))
    session.flush()
    return new
