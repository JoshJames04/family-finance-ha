"""Notification rules engine (issue #116): rule CRUD, trigger
evaluation, batched dispatch, ack/snooze."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import notifications as notify
from app.domain.notifications import NotificationError
from app.persistence.models import (
    NotificationEvent,
    NotificationKind,
    NotificationRule,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _rule(session: Session, household_id: int, rule_id: int
          ) -> NotificationRule:
    r = session.get(NotificationRule, rule_id)
    if r is None or r.household_id != household_id:
        raise HTTPException(404, f"rule {rule_id} not found")
    return r


def _event(session: Session, household_id: int, event_id: int
           ) -> NotificationEvent:
    e = session.get(NotificationEvent, event_id)
    if (e is None or e.rule.household_id != household_id):
        raise HTTPException(404, f"event {event_id} not found")
    return e


@router.get("/rules", response_model=list[schemas.NotificationRuleOut])
def list_rules(
    household: HouseholdDep, session: SessionDep,
) -> list[schemas.NotificationRuleOut]:
    rows = session.scalars(
        select(NotificationRule)
        .where(NotificationRule.household_id == household.id)
        .order_by(NotificationRule.name))
    return [schemas.NotificationRuleOut.model_validate(r)
            for r in rows]


@router.post("/rules", response_model=schemas.NotificationRuleOut,
             status_code=201)
def create_rule(
    body: schemas.NotificationRuleIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.NotificationRuleOut:
    r = notify.create_rule(
        session, household.id, name=body.name,
        kind=NotificationKind(body.kind), params=body.params,
        target_profile_id=body.target_profile_id,
        channel=body.channel, digest=body.digest,
        digest_window_minutes=body.digest_window_minutes)
    emit(session, household.id, actor, "create",
         "notification_rule", r.id, {"kind": body.kind})
    return schemas.NotificationRuleOut.model_validate(r)


@router.patch("/rules/{rule_id}",
              response_model=schemas.NotificationRuleOut)
def toggle_rule(
    rule_id: int, body: dict[str, object], household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.NotificationRuleOut:
    r = _rule(session, household.id, rule_id)
    if "enabled" in body:
        r.enabled = bool(body["enabled"])
    emit(session, household.id, actor, "update",
         "notification_rule", r.id, {"enabled": r.enabled})
    return schemas.NotificationRuleOut.model_validate(r)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> None:
    r = _rule(session, household.id, rule_id)
    emit(session, household.id, actor, "delete",
         "notification_rule", r.id)
    session.delete(r)


@router.post("/rules/{rule_id}/evaluate",
             response_model=dict)
def evaluate_rule(
    rule_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> dict[str, int]:
    """Run the trigger scan now — returns newly-raised event count.
    Re-evaluating the same underlying data can never double-raise
    (dedup_key is unique per rule)."""
    r = _rule(session, household.id, rule_id)
    n = notify.evaluate(session, r)
    emit(session, household.id, actor, "evaluate",
         "notification_rule", r.id, {"raised": n})
    return {"raised": n}


@router.post("/dispatch", response_model=schemas.DispatchOut)
def dispatch(
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.DispatchOut:
    """Flush deliverable events — immediate rules one delivery each,
    digest rules one delivery per expired window batch."""
    result = notify.dispatch(session, household.id)
    emit(session, household.id, actor, "dispatch",
         "notification_delivery", 0,
         {"events_delivered": result.events_delivered})
    return schemas.DispatchOut(
        deliveries=[schemas.NotificationDeliveryOut.model_validate(d)
                    for d in result.deliveries],
        events_delivered=result.events_delivered)


@router.get("/events",
            response_model=list[schemas.NotificationEventOut])
def list_events(
    household: HouseholdDep, session: SessionDep,
) -> list[schemas.NotificationEventOut]:
    rows = session.scalars(
        select(NotificationEvent)
        .join(NotificationRule,
              NotificationEvent.rule_id == NotificationRule.id)
        .where(NotificationRule.household_id == household.id)
        .order_by(NotificationEvent.id.desc()).limit(200))
    return [schemas.NotificationEventOut.model_validate(e)
            for e in rows]


@router.post("/events/{event_id}/ack",
             response_model=schemas.NotificationEventOut)
def ack_event(
    event_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> schemas.NotificationEventOut:
    e = _event(session, household.id, event_id)
    notify.ack(session, e)
    emit(session, household.id, actor, "ack",
         "notification_event", e.id)
    return schemas.NotificationEventOut.model_validate(e)


@router.post("/events/{event_id}/snooze",
             response_model=schemas.NotificationEventOut)
def snooze_event(
    event_id: int, body: dict[str, object], household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.NotificationEventOut:
    e = _event(session, household.id, event_id)
    try:
        notify.snooze(
            session, e, datetime.fromisoformat(str(body["until"])))
    except (NotificationError, KeyError, ValueError, TypeError) as ex:
        raise HTTPException(422, str(ex)) from ex
    emit(session, household.id, actor, "snooze",
         "notification_event", e.id)
    return schemas.NotificationEventOut.model_validate(e)
