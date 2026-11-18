"""Integration event feed (issue #437 / ADR-0009).

The Home Assistant integration drains the durable outbox from its
persisted cursor — committed events are never lost, replays are
idempotent, and payload detail is redacted server-side unless an
admin has opted into ``detailed`` mode (with a privacy warning in
the settings surface).
"""
from __future__ import annotations

import json
from datetime import UTC, date, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, AdminDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import (
    accounts,
    contributions,
    goals,
    ha_grants,
    outbox,
    pay_calendar,
    paydays,
    rbac,
)
from app.domain.money import Money
from app.persistence.models import (
    Account,
    AppMeta,
    Employment,
    ExpectedMovement,
    ExpectedSource,
    IntegrationEvent,
    Role,
    SavingsGoal,
    UserProfile,
)

router = APIRouter(prefix="/integration", tags=["integration"])


@router.get("/items", response_model=schemas.IntegrationItemsOut)
def items(household: HouseholdDep, session: SessionDep, profile: ProfileDep,
          request: Request = None,  # type: ignore[assignment]
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
    # Append after the complete legacy prefix: a payroll destination grant never
    # makes its account balance, cash flow or unrelated records visible.
    if request is not None and getattr(request.state, "service_authenticated", False):
        for employment in session.scalars(select(Employment).where(
                Employment.household_id == household.id, Employment.is_active.is_(True))
                .order_by(Employment.id)):
            if (not ha_grants.profile_allowed(session, household.id, employment.profile_id)
                    or not employment.payday_rule
                    or (employment.start_date is not None and employment.start_date > today)):
                continue
            if employment.paid_into_account_id is not None:
                destination = session.get(Account, employment.paid_into_account_id)
                if destination is None or not ha_grants.account_allowed(
                        session, household.id, destination):
                    continue
            try:
                due = paydays.next_payday(pay_calendar.parse_rule(employment.payday_rule),
                                         after=today - timedelta(days=1))
            except (ValueError, KeyError, TypeError):
                continue
            if employment.end_date is not None and due > employment.end_date:
                continue
            result.append(schemas.IntegrationItemOut(
                family="paydays", id=f"employment:{employment.id}",
                name=f"{employment.employer} · Next payday", value=(due - today).days, unit="d"))
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
           request: Request = None,  # type: ignore[assignment]
           after: Annotated[int, Query(ge=0)] = 0,
           limit: Annotated[int, Query(ge=1, le=500)] = 100,
           latest: Annotated[bool, Query()] = False,
           ) -> schemas.IntegrationFeedOut:
    """Ordered event drain — HA polls from its persisted cursor."""
    profile_id = profile.id if profile and profile.role != Role.viewer else None
    all_scopes = profile is not None and profile.role == Role.admin
    ha_service = request is not None and getattr(request.state, "service_authenticated", False)
    if latest:
        return schemas.IntegrationFeedOut(events=[], next_cursor=outbox.latest_visible_cursor(
            session, household.id, profile_id, all_scopes=all_scopes, ha_service=ha_service))
    detailed = outbox.detail_enabled(session)
    rows, cursor = outbox.feed(session, household.id,
                               after=after, limit=limit)
    return schemas.IntegrationFeedOut(
        events=[_out(e, detailed) for e in rows
                if outbox.event_visible(session, e, profile_id, all_scopes=all_scopes,
                                        ha_service=ha_service)],
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


def _grant_admin(profile: UserProfile | None, household_id: int, request: Request) -> UserProfile:
    settings = request.app.state.settings
    dev_compat = (settings.env == "dev" and not settings.ingress
                  and not getattr(request.state, "service_authenticated", False)
                  and "x-admin-token" not in request.headers)
    if (profile is None or profile.role != Role.admin or profile.household_id != household_id
            or (getattr(request.state, "profile_id", None) != profile.id and not dev_compat)):
        raise HTTPException(403, "an authenticated household administrator is required")
    return profile


@router.get("/private-grants", response_model=schemas.IntegrationGrantsOut)
def get_grants(household: HouseholdDep, session: SessionDep,
               profile: ProfileDep, request: Request) -> schemas.IntegrationGrantsOut:
    _grant_admin(profile, household.id, request)
    profiles = list(session.scalars(select(UserProfile).where(
        UserProfile.household_id == household.id, UserProfile.is_active.is_(True))
        .order_by(UserProfile.id)))
    private_accounts = list(session.scalars(select(Account).where(
        Account.household_id == household.id, Account.archived.is_(False),
        Account.owner_profile_id.is_not(None)).order_by(Account.id)))
    return schemas.IntegrationGrantsOut(
        profile_ids=[p.id for p in profiles
                     if ha_grants.profile_allowed(session, household.id, p.id)],
        account_ids=[a.id for a in private_accounts
                     if ha_grants.account_allowed(session, household.id, a)],
        profiles=[schemas.IntegrationGrantOption(id=p.id, name=p.display_name) for p in profiles],
        accounts=[schemas.IntegrationGrantOption(id=a.id, name=a.name) for a in private_accounts])


@router.put("/private-grants", response_model=schemas.IntegrationGrantsOut)
def put_grants(body: schemas.IntegrationGrantsIn, household: HouseholdDep, session: SessionDep,
               profile: ProfileDep, request: Request) -> schemas.IntegrationGrantsOut:
    admin = _grant_admin(profile, household.id, request)
    options = get_grants(household, session, profile, request)
    if (set(body.profile_ids) - {p.id for p in options.profiles}
            or set(body.account_ids) - {a.id for a in options.accounts}):
        raise HTTPException(422, "select active profiles and private accounts from this household")
    previous = ha_grants.stored(session, household.id)
    accounts_by_id = {a.id: a for a in session.scalars(select(Account).where(
        Account.household_id == household.id, Account.id.in_(body.account_ids)))}
    value = {"profile_ids": sorted(set(body.profile_ids)), "accounts": {
        str(aid): accounts_by_id[aid].owner_profile_id for aid in sorted(set(body.account_ids))}}
    row = session.get(AppMeta, ha_grants.key(household.id))
    if row is None:
        session.add(AppMeta(key=ha_grants.key(household.id), value=json.dumps(value)))
    else:
        row.value = json.dumps(value)
    session.flush()
    emit(session, household.id, f"profile:{admin.id}", "integration_grants",
         "integration_settings", 0, {"old": previous, "new": value})
    return get_grants(household, session, profile, request)
