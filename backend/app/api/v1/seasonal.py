"""Seasonal events + gifts (#96)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import financial_events, rbac, seasonal
from app.persistence.models import (
    Account,
    Gift,
    PurchaseStatus,
    SavingsGoal,
    SeasonalEvent,
    Transaction,
    UserProfile,
)


def _authorize(request: Request, household: HouseholdDep,
               session: SessionDep, profile: ProfileDep) -> None:
    for key in ("eid", "other_id"):
        raw = request.path_params.get(key)
        if raw is None:
            continue
        try:
            event_id = int(raw)
        except ValueError as error:
            raise HTTPException(404, "seasonal event not found") from error
        event = _event(session, household.id, event_id)
        if not rbac.visible(event.owner_profile_id, profile):
            raise HTTPException(404, "seasonal event not found")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not rbac.write_allowed(
                event.owner_profile_id, profile):
            raise HTTPException(403, "role cannot mutate this scope")


router = APIRouter(prefix="/seasonal-events", tags=["seasonal-events"],
                   dependencies=[Depends(_authorize)])


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
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
) -> list[SeasonalEvent]:
    return [event for event in session.execute(
        select(SeasonalEvent).where(
            SeasonalEvent.household_id == household.id)
        .order_by(SeasonalEvent.year.desc(), SeasonalEvent.type))
        .scalars() if rbac.visible(event.owner_profile_id, profile)]


@router.post("", response_model=schemas.SeasonalEventOut,
             status_code=201)
def create_event(
    body: schemas.SeasonalEventIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> SeasonalEvent:
    if not rbac.write_allowed(body.owner_profile_id, profile):
        raise HTTPException(403, "role cannot mutate this scope")
    if body.owner_profile_id is not None:
        owner = session.get(UserProfile, body.owner_profile_id)
        if owner is None or owner.household_id != household.id:
            raise HTTPException(404, "owner profile not found")
    if body.savings_goal_id is not None:
        g = session.get(SavingsGoal, body.savings_goal_id)
        if g is None or g.household_id != household.id:
            raise HTTPException(
                404, f"savings goal {body.savings_goal_id} not found")
        account = session.get(Account, g.account_id)
        if account is None or not rbac.visible(account.owner_profile_id, profile):
            raise HTTPException(404, "savings goal not found")
        if (account.owner_profile_id is not None
                and body.owner_profile_id != account.owner_profile_id):
            raise HTTPException(409, "event must use the private funding account owner")
    e = SeasonalEvent(
        household_id=household.id, year=body.year, type=body.type,
        name=body.name, budget_pence=body.budget.pence,
        owner_profile_id=body.owner_profile_id,
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


@router.get("/{eid}/compare/{other_id}",
            response_model=schemas.EventComparisonOut)
def compare_events(
    eid: int, other_id: int, household: HouseholdDep,
    session: SessionDep,
) -> schemas.EventComparisonOut:
    """YoY comparison of this event vs another (issue #99): totals,
    per-recipient drift, average spend per priced gift."""
    a = _event(session, household.id, eid)
    b = _event(session, household.id, other_id)
    earlier, later = (a, b) if a.year < b.year else (b, a)
    c = seasonal.compare(session, earlier, later)
    return schemas.EventComparisonOut(
        earlier=schemas.EventTotalsOut(**vars(c.earlier)),
        later=schemas.EventTotalsOut(**vars(c.later)),
        actual_delta_pence=c.actual_delta_pence,
        earlier_avg_gift_pence=c.earlier_avg_gift_pence,
        later_avg_gift_pence=c.later_avg_gift_pence,
        recipients=[schemas.RecipientComparisonOut(**vars(r))
                    for r in c.recipients])


@router.post("/{eid}/gifts",
             response_model=schemas.GiftOut, status_code=201)
def add_gift(
    eid: int, body: schemas.GiftIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> Gift:
    e = _event(session, household.id, eid)
    financial_events.evaluate_seasonal(session, household.id)
    g = Gift(event_id=e.id, recipient=body.recipient, idea=body.idea,
             planned_pence=body.planned.pence,
             actual_pence=body.actual.pence if body.actual else None,
             notes=body.notes)
    session.add(g)
    session.flush()
    emit(session, household.id, actor, "create", "gift", g.id,
         {"event_id": e.id, "recipient": g.recipient})
    financial_events.evaluate_seasonal(session, household.id)
    return g


@router.get("/{eid}/gifts", response_model=list[schemas.GiftOut])
def list_gifts(
    eid: int, household: HouseholdDep, session: SessionDep,
) -> list[Gift]:
    e = _event(session, household.id, eid)
    return list(e.gifts)


@router.post("/{eid}/rollover",
             response_model=schemas.SeasonalEventOut, status_code=201)
def rollover_event(
    eid: int, body: schemas.EventRolloverIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> SeasonalEvent:
    e = _event(session, household.id, eid)
    new = seasonal.rollover(
        session, e, body.year, copy_planned=body.copy_planned,
        carry_remaining=body.carry_remaining)
    emit(session, household.id, actor, "rollover", "seasonal_event",
         new.id, {"from_event_id": e.id, "year": new.year})
    return new


@router.post("/{eid}/gifts/{gid}/transition",
             response_model=schemas.GiftOut)
def transition_gift(
    eid: int, gid: int, body: schemas.GiftTransitionIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> Gift:
    e = _event(session, household.id, eid)
    financial_events.evaluate_seasonal(session, household.id)
    g = _gift(session, e, gid)
    seasonal.transition_purchase(g, body.to)
    session.flush()
    emit(session, household.id, actor, "transition", "gift", g.id,
         {"to": body.to.value})
    financial_events.evaluate_seasonal(session, household.id)
    return g


@router.post("/{eid}/gifts/{gid}/pay",
             response_model=schemas.GiftOut)
def pay_gift(
    eid: int, gid: int, body: schemas.GiftPayIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> Gift:
    e = _event(session, household.id, eid)
    financial_events.evaluate_seasonal(session, household.id)
    g = _gift(session, e, gid)
    tx: Transaction | None = None
    if body.transaction_id is not None:
        tx = session.get(Transaction, body.transaction_id)
        if tx is None:
            raise HTTPException(
                404, f"transaction {body.transaction_id} not found")
    if tx is not None and not rbac.visible(tx.account.owner_profile_id, profile):
        raise HTTPException(404, "transaction not found")
    if (tx is not None and tx.account.owner_profile_id is not None
            and tx.account.owner_profile_id != e.owner_profile_id):
        raise HTTPException(409, "private payment requires an event with the same owner")
    if body.actual is not None:
        g.actual_pence = body.actual.pence
    seasonal.mark_paid(session, g, tx)
    emit(session, household.id, actor, "pay", "gift", g.id,
         {"event_id": e.id})
    financial_events.evaluate_seasonal(session, household.id)
    return g


@router.delete("/{eid}/gifts/{gid}", status_code=204)
def delete_gift(
    eid: int, gid: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> None:
    e = _event(session, household.id, eid)
    financial_events.evaluate_seasonal(session, household.id)
    g = _gift(session, e, gid)
    if g.purchase_status not in (PurchaseStatus.idea,
                                 PurchaseStatus.planned):
        raise HTTPException(
            409, "only idea/planned gifts can be deleted")
    session.delete(g)
    session.flush()
    emit(session, household.id, actor, "delete", "gift", gid,
         {"event_id": e.id})

    financial_events.evaluate_seasonal(session, household.id)
