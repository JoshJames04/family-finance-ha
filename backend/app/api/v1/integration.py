"""Integration event feed (issue #437 / ADR-0009).

The Home Assistant integration drains the durable outbox from its
persisted cursor — committed events are never lost, replays are
idempotent, and payload detail is redacted server-side unless an
admin has opted into ``detailed`` mode (with a privacy warning in
the settings surface).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, AdminDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import outbox
from app.persistence.models import IntegrationEvent, Role

router = APIRouter(prefix="/integration", tags=["integration"])


def _out(ev: IntegrationEvent, detailed: bool) -> schemas.IntegrationEventOut:
    return schemas.IntegrationEventOut(
        seq=ev.id, event_type=ev.event_type,
        entity_kind=ev.entity_kind, entity_id=ev.entity_id,
        occurred_at=ev.occurred_at,
        data=outbox.visible_payload(ev, detailed))


@router.get("/events", response_model=schemas.IntegrationFeedOut)
def events(household: HouseholdDep, session: SessionDep, profile: ProfileDep = None,
           after: Annotated[int, Query(ge=0)] = 0,
           limit: Annotated[int, Query(ge=1, le=500)] = 100,
           latest: Annotated[bool, Query()] = False,
           ) -> schemas.IntegrationFeedOut:
    """Ordered event drain — HA polls from its persisted cursor."""
    if latest:
        return schemas.IntegrationFeedOut(events=[], next_cursor=outbox.latest_visible_cursor(
            session, household.id, profile.id if profile and profile.role != Role.viewer else None))
    detailed = outbox.detail_enabled(session)
    rows, cursor = outbox.feed(session, household.id,
                               after=after, limit=limit)
    return schemas.IntegrationFeedOut(
        events=[_out(e, detailed) for e in rows
                if outbox.event_visible(session, e, profile.id if profile else None)],
        next_cursor=cursor)


@router.get("/settings", response_model=schemas.IntegrationSettingsOut)
def get_settings(household: HouseholdDep, session: SessionDep,
                 ) -> schemas.IntegrationSettingsOut:
    return schemas.IntegrationSettingsOut(
        detail="detailed" if outbox.detail_enabled(session) else "safe")


@router.patch("/settings", response_model=schemas.IntegrationSettingsOut)
def patch_settings(body: schemas.IntegrationSettingsPatch,
                   household: HouseholdDep, session: SessionDep,
                   actor: ActorDep, _admin: AdminDep,
                   ) -> schemas.IntegrationSettingsOut:
    """Financial detail on the HA event bus is explicit opt-in:
    names and amounts may persist in bus events, recorder, traces
    and notification providers. Admin-only; the change is audited
    and takes effect on the next poll."""
    if body.detail not in ("safe", "detailed"):
        raise HTTPException(
            422, "detail must be 'safe' or 'detailed'")
    old = "detailed" if outbox.detail_enabled(session) else "safe"
    outbox.set_detail(session, body.detail)
    emit(session, household.id, actor, "integration_detail",
         "integration_settings", 0,
         {"old": old, "new": body.detail})
    return schemas.IntegrationSettingsOut(detail=body.detail)
