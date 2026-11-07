"""Seasonal events + gifts (#96)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import seasonal
from app.persistence.models import (
    Gift,
    PurchaseStatus,
    SavingsGoal,
    SeasonalEvent,
    Transaction,
)

router = APIRouter(prefix="/seasonal-events", tags=["seasonal-events"])


def _event(session: Session, household_id: int,
           eid: int) -> SeasonalEvent:
    e = session.get(SeasonalEvent, eid)
    if e is None or e.household_id != household_id:
        raise HTTPException(404, f"seasonal event {eid} not found")
    return e


def _gift(session: Session, event: SeasonalEvent, gid: int) -> Gift:
    g = session.get(Gift, gid)
    if g is None or g.event_id != event.id:
        raise HTTPException(404, f"gift {gid} not found")
    return g


@router.get("", response_model=list[schemas.SeasonalEventOut])
def list_events(
    household: HouseholdDep, session: SessionDep,
) -> list[SeasonalEvent]:
    return list(session.execute(
        select(SeasonalEvent).where(
            SeasonalEvent.household_id == household.id)
        .order_by(SeasonalEvent.year.desc(), SeasonalEvent.type))
        .scalars())


@router.post("", response_model=schemas.SeasonalEventOut,
             status_code=201)
def create_event(
    body: schemas.SeasonalEventIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> SeasonalEvent:
    if body.savings_goal_id is not None:
        g = session.get(SavingsGoal, body.savings_goal_id)
        if g is None or g.household_id != household.id:
            raise HTTPException(
                404, f"savings goal {body.savings_goal_id} not found")
    e = SeasonalEvent(
        household_id=household.id, year=body.year, type=body.type,
        name=body.name, budget_pence=body.budget.pence,
        savings_goal_id=body.savings_goal_id, notes=body.notes)
    session.add(e)
    session.flush()
    emit(session, household.id, actor, "create", "seasonal_event",
         e.id, {"name": e.name, "type": e.type, "year": e.year})
    return e


@router.get("/{eid}",
            response_model=schemas.SeasonalEventOut)
def get_event(
    eid: int, household: HouseholdDep, session: SessionDep,
) -> SeasonalEvent:
    return _event(session, household.id, eid)


@router.get("/{eid}/totals",
            response_model=schemas.EventTotalsOut)
def event_totals(
    eid: int, household: HouseholdDep, session: SessionDep,
) -> schemas.EventTotalsOut:
    e = _event(session, household.id, eid)
    return schemas.EventTotalsOut(**vars(seasonal.totals(session, e)))


@router.post("/{eid}/gifts",
             response_model=schemas.GiftOut, status_code=201)
def add_gift(
    eid: int, body: schemas.GiftIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> Gift:
    e = _event(session, household.id, eid)
    g = Gift(event_id=e.id, recipient=body.recipient, idea=body.idea,
             planned_pence=body.planned.pence,
             actual_pence=body.actual.pence if body.actual else None,
             notes=body.notes)
    session.add(g)
    session.flush()
    emit(session, household.id, actor, "create", "gift", g.id,
         {"event_id": e.id, "recipient": g.recipient})
    return g


@router.get("/{eid}/gifts", response_model=list[schemas.GiftOut])
def list_gifts(
    eid: int, household: HouseholdDep, session: SessionDep,
) -> list[Gift]:
    e = _event(session, household.id, eid)
    return list(e.gifts)


@router.post("/{eid}/gifts/{gid}/transition",
             response_model=schemas.GiftOut)
def transition_gift(
    eid: int, gid: int, body: schemas.GiftTransitionIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> Gift:
    e = _event(session, household.id, eid)
    g = _gift(session, e, gid)
    seasonal.transition_purchase(g, body.to)
    session.flush()
    emit(session, household.id, actor, "transition", "gift", g.id,
         {"to": body.to.value})
    return g


@router.post("/{eid}/gifts/{gid}/pay",
             response_model=schemas.GiftOut)
def pay_gift(
    eid: int, gid: int, body: schemas.GiftPayIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> Gift:
    e = _event(session, household.id, eid)
    g = _gift(session, e, gid)
    tx: Transaction | None = None
    if body.transaction_id is not None:
        tx = session.get(Transaction, body.transaction_id)
        if tx is None:
            raise HTTPException(
                404, f"transaction {body.transaction_id} not found")
    if body.actual is not None:
        g.actual_pence = body.actual.pence
    seasonal.mark_paid(session, g, tx)
    emit(session, household.id, actor, "pay", "gift", g.id,
         {"event_id": e.id})
    return g


@router.delete("/{eid}/gifts/{gid}", status_code=204)
def delete_gift(
    eid: int, gid: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> None:
    e = _event(session, household.id, eid)
    g = _gift(session, e, gid)
    if g.purchase_status not in (PurchaseStatus.idea,
                                 PurchaseStatus.planned):
        raise HTTPException(
            409, "only idea/planned gifts can be deleted")
    session.delete(g)
    session.flush()
    emit(session, household.id, actor, "delete", "gift", gid,
         {"event_id": e.id})
