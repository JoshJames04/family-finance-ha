"""Notification rules engine (issue #116): rule CRUD, trigger
evaluation, batched dispatch, ack/snooze."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import (
    ActorDep,
    HouseholdDep,
    ProfileDep,
    SessionDep,
)
from app.domain import deliver, rbac
from app.domain import notifications as notify
from app.domain.notifications import NotificationError
from app.persistence.models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationKind,
    NotificationRule,
    Role,
    UserProfile,
)


def _write_guard(request: Request, profile: ProfileDep) -> None:
    denied = (profile is None and request.app.state.settings.ingress) or not rbac.write_allowed(
        None, profile)
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and denied:
        raise HTTPException(403, "notification write permission required")


router = APIRouter(prefix="/notifications", tags=["notifications"],
                   dependencies=[Depends(_write_guard)])


def _rule(session: Session, household_id: int, rule_id: int,
          profile: UserProfile | None = None
          ) -> NotificationRule:
    r = session.get(NotificationRule, rule_id)
    if (r is None or r.household_id != household_id
            or (profile is not None and profile.role != Role.admin
                and r.target_profile_id not in {None, profile.id})):
        raise HTTPException(404, f"rule {rule_id} not found")
    return r


def _event(session: Session, household_id: int, event_id: int,
           profile: UserProfile | None = None
           ) -> NotificationEvent:
    e = session.get(NotificationEvent, event_id)
    if (e is None or e.rule.household_id != household_id
            or (profile is not None and profile.role != Role.admin
                and e.rule.target_profile_id not in {None, profile.id})):
        raise HTTPException(404, f"event {event_id} not found")
    return e


@router.get("/rules", response_model=list[schemas.NotificationRuleOut])
def list_rules(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
) -> list[schemas.NotificationRuleOut]:
    rows = session.scalars(
        select(NotificationRule)
        .where(NotificationRule.household_id == household.id,
               or_(NotificationRule.target_profile_id.is_(None),
                   NotificationRule.target_profile_id == (
                       profile.id if profile and profile.role != Role.viewer else None)))
        .order_by(NotificationRule.name))
    return [schemas.NotificationRuleOut.model_validate(r)
            for r in rows]


@router.post("/rules", response_model=schemas.NotificationRuleOut,
             status_code=201)
def create_rule(
    body: schemas.NotificationRuleIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.NotificationRuleOut:
    if (profile is not None and profile.role != Role.admin
            and body.target_profile_id not in {None, profile.id}):
        raise HTTPException(403, "cannot target another member")
    r = notify.create_rule(
        session, household.id, name=body.name,
        kind=NotificationKind(body.kind), params=body.params,
        target_profile_id=body.target_profile_id,
        channel=body.channel, digest=body.digest,
        digest_window_minutes=body.digest_window_minutes,
        service=body.service, privacy_safe=body.privacy_safe)
    emit(session, household.id, actor, "create",
         "notification_rule", r.id, {"kind": body.kind})
    return schemas.NotificationRuleOut.model_validate(r)


@router.patch("/rules/{rule_id}",
              response_model=schemas.NotificationRuleOut)
def toggle_rule(
    rule_id: int, body: dict[str, object], household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.NotificationRuleOut:
    r = _rule(session, household.id, rule_id, profile)
    if "enabled" in body:
        r.enabled = bool(body["enabled"])
    emit(session, household.id, actor, "update",
         "notification_rule", r.id, {"enabled": r.enabled})
    return schemas.NotificationRuleOut.model_validate(r)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, profile: ProfileDep,
) -> None:
    r = _rule(session, household.id, rule_id, profile)
    emit(session, household.id, actor, "delete",
         "notification_rule", r.id)
    session.delete(r)


@router.post("/rules/{rule_id}/evaluate",
             response_model=dict)
def evaluate_rule(
    rule_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, profile: ProfileDep,
) -> dict[str, int]:
    """Run the trigger scan now — returns newly-raised event count.
    Re-evaluating the same underlying data can never double-raise
    (dedup_key is unique per rule)."""
    r = _rule(session, household.id, rule_id, profile)
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
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
) -> list[schemas.NotificationEventOut]:
    rows = session.scalars(
        select(NotificationEvent)
        .join(NotificationRule,
              NotificationEvent.rule_id == NotificationRule.id)
        .where(NotificationRule.household_id == household.id,
               or_(NotificationRule.target_profile_id.is_(None),
                   NotificationRule.target_profile_id == (
                       profile.id if profile and profile.role != Role.viewer else None)))
        .order_by(NotificationEvent.id.desc()).limit(200))
    return [schemas.NotificationEventOut.model_validate(e)
            for e in rows]


@router.post("/events/{event_id}/ack",
             response_model=schemas.NotificationEventOut)
def ack_event(
    event_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, profile: ProfileDep,
) -> schemas.NotificationEventOut:
    e = _event(session, household.id, event_id, profile)
    notify.ack(session, e)
    emit(session, household.id, actor, "ack",
         "notification_event", e.id)
    return schemas.NotificationEventOut.model_validate(e)


@router.post("/events/{event_id}/snooze",
             response_model=schemas.NotificationEventOut)
def snooze_event(
    event_id: int, body: dict[str, object], household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.NotificationEventOut:
    e = _event(session, household.id, event_id, profile)
    try:
        notify.snooze(
            session, e, datetime.fromisoformat(str(body["until"])))
    except (NotificationError, KeyError, ValueError, TypeError) as ex:
        raise HTTPException(422, str(ex)) from ex
    emit(session, household.id, actor, "snooze",
         "notification_event", e.id)
    return schemas.NotificationEventOut.model_validate(e)


class _UrllibNotifier:
    """Prod transport: POST {ha_url}/api/services/notify/{service}
    with the supervisor token — no new deps (issue #119)."""

    def __init__(self, ha_url: str, token: str | None) -> None:
        self._url = ha_url.rstrip("/")
        self._token = token

    def notify(self, service: str | None, title: str,
               message: str) -> None:
        import json as _json
        import urllib.request
        svc = service or "notify"
        import re
        if re.fullmatch(r"[a-z0-9_]+", svc) is None:
            raise ValueError("invalid notification service")
        req = urllib.request.Request(
            f"{self._url}/api/services/notify/{svc}",
            data=_json.dumps(
                {"title": title, "message": message}).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self._token}"}
                   if self._token else {})})
        with urllib.request.urlopen(req, timeout=10):
            pass


def _notifier(request: Request) -> deliver.HaNotifier:
    """Injectable transport — tests swap app.state.ha_notifier."""
    override: deliver.HaNotifier | None = getattr(
        request.app.state, "ha_notifier", None)
    if override is not None:
        return override
    settings = request.app.state.settings
    token = (settings.supervisor_token.get_secret_value()
             if settings.supervisor_token is not None else None)
    return _UrllibNotifier(settings.ha_url, token)


@router.post("/deliver", response_model=schemas.DeliverOut)
def deliver_now(
    request: Request, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
) -> schemas.DeliverOut:
    """Attempt every pending delivery (issue #119). Per-delivery
    failures land on the row and surface here — the endpoint always
    reports, never crashes."""
    res = deliver.deliver_pending(
        session, household.id, _notifier(request))
    return schemas.DeliverOut(sent=res.sent, failed=res.failed)


@router.get("/deliveries",
            response_model=list[schemas.NotificationDeliveryOut])
def list_deliveries(
    household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
) -> list[schemas.NotificationDeliveryOut]:
    """Delivery monitoring — failed rows surface their error."""
    rows = session.scalars(
        select(NotificationDelivery).join(NotificationRule,
            NotificationDelivery.rule_id == NotificationRule.id).where(
            NotificationDelivery.household_id == household.id,
            or_(NotificationRule.target_profile_id.is_(None),
                NotificationRule.target_profile_id == (
                    profile.id if profile and profile.role != Role.viewer else None)))
        .order_by(NotificationDelivery.id.desc()).limit(200)).all()
    return [schemas.NotificationDeliveryOut.model_validate(d)
            for d in rows]
