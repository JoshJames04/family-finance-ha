"""Integration event feed (issue #437 / ADR-0009).

The Home Assistant integration drains the durable outbox from its
persisted cursor — committed events are never lost, replays are
idempotent, and payload detail is redacted server-side unless an
admin has opted into ``detailed`` mode (with a privacy warning in
the settings surface).
"""
from __future__ import annotations

from datetime import UTC, date, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, AdminDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import accounts, contributions, goals, outbox, rbac
from app.domain.money import Money
from app.persistence.models import (
    Account,
    ExpectedMovement,
    ExpectedSource,
    IntegrationEvent,
    Role,
    SavingsGoal,
)

router = APIRouter(prefix="/integration", tags=["integration"])


@router.get("/items", response_model=schemas.IntegrationItemsOut)
def items(household: HouseholdDep, session: SessionDep, profile: ProfileDep,
          limit: Annotated[int, Query(ge=1, le=100)] = 100) -> schemas.IntegrationItemsOut:
    """Opt-in HA discovery; cap entities and apply ledger visibility first."""
    visible = [a for a in session.scalars(select(Account).where(
        Account.household_id == household.id, Account.archived.is_(False)).order_by(Account.id))
        if rbac.visible(a.owner_profile_id, profile)]
    allowed = {a.id for a in visible}
    result = [schemas.IntegrationItemOut(family="accounts", id=f"account:{a.id}", name=a.name,
              value=Money.of_pence(accounts.current_balance(session, a.id)).pounds_decimal(),
              unit=a.currency)
              for a in visible]
    for goal in session.scalars(select(SavingsGoal).where(
            SavingsGoal.household_id == household.id, SavingsGoal.active.is_(True),
            SavingsGoal.account_id.in_(allowed)).order_by(SavingsGoal.id)):
        result.append(schemas.IntegrationItemOut(family="goals", id=f"goal:{goal.id}",
                      name=goal.name,
                      value=goals.progress(session, goal).pct, unit="%"))
    today = date.today()
    seen: set[str] = set()
    movements = session.scalars(select(ExpectedMovement).where(
        ExpectedMovement.household_id == household.id,
        ExpectedMovement.dismissed_at.is_(None), ExpectedMovement.amount_pence < 0,
        ExpectedMovement.expected_date >= today - timedelta(days=365),
        ExpectedMovement.expected_date <= today + timedelta(days=365),
        ExpectedMovement.source_type.in_([ExpectedSource.commitment, ExpectedSource.subscription])
    ).order_by(ExpectedMovement.expected_date, ExpectedMovement.id))
    for movement in movements:
        if movement.account_id is not None and movement.account_id not in allowed:
            continue
        key = f"{movement.source_type.value}:{movement.source_id}"
        if key in seen or contributions.status(session, movement).outstanding_pence <= 0:
            continue
        seen.add(key)
        result.append(schemas.IntegrationItemOut(family="bills", id=key, name=movement.label,
                      value=(movement.expected_date - today).days, unit="d"))
    return schemas.IntegrationItemsOut(items=result[:limit])


def _out(ev: IntegrationEvent, detailed: bool) -> schemas.IntegrationEventOut:
    return schemas.IntegrationEventOut(
        seq=ev.id, event_type=ev.event_type,
        entity_kind=ev.entity_kind, entity_id=ev.entity_id,
        occurred_at=(ev.occurred_at.replace(tzinfo=UTC) if ev.occurred_at.tzinfo is None
                     else ev.occurred_at.astimezone(UTC)),
        data=outbox.visible_payload(ev, detailed))


@router.get("/events", response_model=schemas.IntegrationFeedOut)
def events(household: HouseholdDep, session: SessionDep, profile: ProfileDep = None,
           after: Annotated[int, Query(ge=0)] = 0,
           limit: Annotated[int, Query(ge=1, le=500)] = 100,
           latest: Annotated[bool, Query()] = False,
           ) -> schemas.IntegrationFeedOut:
    """Ordered event drain — HA polls from its persisted cursor."""
    profile_id = profile.id if profile and profile.role != Role.viewer else None
    all_scopes = profile is not None and profile.role == Role.admin
    if latest:
        return schemas.IntegrationFeedOut(events=[], next_cursor=outbox.latest_visible_cursor(
            session, household.id, profile_id, all_scopes=all_scopes))
    detailed = outbox.detail_enabled(session)
    rows, cursor = outbox.feed(session, household.id,
                               after=after, limit=limit)
    return schemas.IntegrationFeedOut(
        events=[_out(e, detailed) for e in rows
                if outbox.event_visible(session, e, profile_id, all_scopes=all_scopes)],
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
