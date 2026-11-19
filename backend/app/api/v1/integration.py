"""Integration event feed (issue #437 / ADR-0009).

The Home Assistant integration drains the durable outbox from its
persisted cursor — committed events are never lost, replays are
idempotent, and payload detail is redacted server-side unless an
admin has opted into ``detailed`` mode (with a privacy warning in
the settings surface).
"""
from __future__ import annotations

from datetime import UTC, date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, AdminDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import (
    accounts,
    budget_periods,
    budgets,
    contributions,
    goals,
    outbox,
    projection,
    rbac,
    seasonal,
)
from app.domain.money import Money
from app.persistence.models import (
    Account,
    Budget,
    BudgetLineSide,
    BudgetStatus,
    Employment,
    ExpectedMovement,
    ExpectedSource,
    IntegrationEvent,
    PayrollRun,
    Role,
    SavingsGoal,
    SeasonalEvent,
    UserProfile,
)

router = APIRouter(prefix="/integration", tags=["integration"])


@router.get("/items", response_model=schemas.IntegrationItemsOut)
def items(household: HouseholdDep, session: SessionDep, profile: ProfileDep,
          limit: Annotated[int, Query(ge=1, le=100)] = 100,
          offset: Annotated[int, Query(ge=0)] = 0,
          forecast_days: Annotated[int, Query(ge=1, le=projection.MAX_HORIZON_DAYS)]
          = projection.DEFAULT_HORIZON_DAYS) -> schemas.IntegrationItemsOut:
    """Opt-in HA discovery; cap entities and apply ledger visibility first."""
    visible = [a for a in session.scalars(select(Account).where(
        Account.household_id == household.id, Account.archived.is_(False)).order_by(Account.id))
        if rbac.visible(a.owner_profile_id, profile)]
    allowed = {a.id for a in visible}
    today = date.today()
    result = [schemas.IntegrationItemOut(family="accounts", id=f"account:{a.id}", name=a.name,
              value=Money.of_pence(accounts.current_balance(session, a.id)).pounds_decimal(),
              unit=a.currency)
              for a in visible]
    goal_criteria: list[schemas.IntegrationItemOut] = []
    for goal in session.scalars(select(SavingsGoal).where(
            SavingsGoal.household_id == household.id, SavingsGoal.active.is_(True),
            SavingsGoal.account_id.in_(allowed)).order_by(SavingsGoal.id)):
        progress = goals.progress(session, goal)
        result.append(schemas.IntegrationItemOut(family="goals", id=f"goal:{goal.id}",
                      name=goal.name,
                      value=progress.pct, unit="%"))
        goal_criteria.append(schemas.IntegrationItemOut(
            family="goal_funding", id=f"goal:{goal.id}", name=f"{goal.name} funded",
            value=Money.of_pence(progress.contributed_pence).pounds_decimal(), unit="GBP"))
        if goal.target_date is not None:
            goal_criteria.append(schemas.IntegrationItemOut(
                family="goal_dates", id=f"goal:{goal.id}:days",
                name=f"{goal.name} days to target",
                value=(goal.target_date - today).days, unit="d"))
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
    # Legacy clients read only the first 100 rows. Preserve their complete old
    # accounts/goals/bills prefix before appending any newly discoverable metric.
    result.extend(goal_criteria)
    result.extend(_criteria_items(session, household.id, profile, visible, today, forecast_days))
    return schemas.IntegrationItemsOut(items=result[offset:offset + limit],
                                      has_more=offset + limit < len(result))


def _employment(session: Session, movement: ExpectedMovement) -> Employment | None:
    if movement.source_type == ExpectedSource.employment:
        return session.get(Employment, movement.source_id)
    run = session.get(PayrollRun, movement.source_id)
    return run.employment if run is not None else None


def _criteria_items(session: Session, household_id: int, profile: UserProfile | None,
                    visible: list[Account], today: date,
                    forecast_days: int) -> list[schemas.IntegrationItemOut]:
    result: list[schemas.IntegrationItemOut] = []
    horizon = today + timedelta(days=forecast_days)
    # Account visibility alone cannot authorise a private employment forecast
    # whose destination happens to be shared. Omit that derived account metric.
    hidden_forecasts: set[int] = set()
    paydays: dict[int, tuple[Employment, date]] = {}
    for movement in session.scalars(select(ExpectedMovement).where(
            ExpectedMovement.household_id == household_id,
            ExpectedMovement.expected_date >= today,
            ExpectedMovement.expected_date <= today + timedelta(days=projection.MAX_HORIZON_DAYS),
            ExpectedMovement.dismissed_at.is_(None),
            ExpectedMovement.source_type.in_(
                [ExpectedSource.employment, ExpectedSource.payroll_run])
    ).order_by(ExpectedMovement.expected_date, ExpectedMovement.id)):
        if contributions.status(session, movement).outstanding_pence <= 0:
            continue
        employment = _employment(session, movement)
        if (employment is None or employment.household_id != household_id
                or not rbac.visible(employment.profile_id, profile)):
            if today < movement.expected_date <= horizon and movement.account_id is not None:
                hidden_forecasts.add(movement.account_id)
            continue
        if (movement.amount_pence > 0 and employment.is_active
                and (employment.end_date is None or employment.end_date >= movement.expected_date)
                and (employment.start_date is None
                     or employment.start_date <= movement.expected_date)
                and (movement.account_id is None
                     or any(a.id == movement.account_id for a in visible))):
            paydays.setdefault(employment.id, (employment, movement.expected_date))
    for account in visible:
        if account.id in hidden_forecasts:
            continue
        forecast = projection.project(session, account, forecast_days, today)
        result.extend([
            schemas.IntegrationItemOut(family="safe_to_spend", id=f"account:{account.id}",
                name=f"{account.name} safe to spend",
                value=Money.of_pence(forecast.safe_to_spend_pence).pounds_decimal(), unit="GBP"),
            schemas.IntegrationItemOut(family="projected_balances", id=f"account:{account.id}",
                name=f"{account.name} lowest projected balance ({forecast_days} days)",
                value=Money.of_pence(forecast.min_balance_pence).pounds_decimal(), unit="GBP"),
        ])
    for budget in session.scalars(select(Budget).where(
            Budget.household_id == household_id,
            Budget.status.in_([BudgetStatus.approved, BudgetStatus.locked])).order_by(Budget.id)):
        if not rbac.visible(budget.owner_profile_id, profile):
            continue
        period = budget_periods.resolve_period(session, budget, basis="pay_cycle")
        if period.warning:
            continue  # A missing/ambiguous personal cycle is unknown, not a calendar substitute.
        summary = budgets.spending_summary(session, budget, as_of=today,
                                           start=period.start, end=period.end)
        spent = int(summary["spent_pence"])
        planned = sum(line.effective_pence for line in budget.lines
                      if line.side == BudgetLineSide.expense)
        name = f"{budget.name} ({period.start}–{period.end}, {period.basis.replace('_', ' ')})"
        result.append(schemas.IntegrationItemOut(
            family="budget_remaining", id=f"budget:{budget.id}", name=f"{name} remaining",
            value=Money.of_pence(planned - spent).pounds_decimal(), unit="GBP"))
        if planned > 0:
            result.append(schemas.IntegrationItemOut(
                family="budget_usage", id=f"budget:{budget.id}", name=f"{name} usage",
                value=(Decimal(spent) * 100 / planned).quantize(Decimal("0.01")), unit="%"))
    for event in session.scalars(select(SeasonalEvent).where(
            SeasonalEvent.household_id == household_id).order_by(SeasonalEvent.id)):
        if not rbac.visible(event.owner_profile_id, profile):
            continue
        totals = seasonal.totals(session, event)
        name = f"{event.name} ({event.year})"
        if event.event_date is not None:
            result.append(schemas.IntegrationItemOut(
                family="seasonal_dates", id=f"seasonal:{event.id}:days",
                name=f"{name} days to event", value=(event.event_date - today).days, unit="d"))
        result.append(schemas.IntegrationItemOut(
            family="seasonal_remaining", id=f"seasonal:{event.id}", name=f"{name} remaining",
            value=Money.of_pence(totals.remaining_pence).pounds_decimal(), unit="GBP"))
        if event.budget_pence > 0:
            result.append(schemas.IntegrationItemOut(
                family="seasonal_usage", id=f"seasonal:{event.id}", name=f"{name} usage",
                value=(Decimal(totals.actual_pence) * 100 / event.budget_pence)
                .quantize(Decimal("0.01")), unit="%"))
    for employment, due in paydays.values():
        result.append(schemas.IntegrationItemOut(
            family="paydays", id=f"employment:{employment.id}",
            name=f"{employment.employer} days to payday", value=(due - today).days, unit="d"))
    return result


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
