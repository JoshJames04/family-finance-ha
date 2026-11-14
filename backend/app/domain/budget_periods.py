"""Read-only reporting windows over nominal, unchanged monthly plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.pay_calendar import parse_rule
from app.domain.paydays import PaydayRule, RuleKind, ShiftPolicy, pay_date
from app.persistence.models import (
    AppMeta,
    Budget,
    BudgetPeriodType,
    Employment,
    UserProfile,
)


@dataclass(frozen=True)
class BudgetWindow:
    start: date
    end: date
    basis: str
    employment_id: int | None = None
    warning: str | None = None


class SourceChoice(TypedDict):
    employment_id: int
    employer: str


def _anchor_key(household_id: int, profile_id: int) -> str:
    return f"budget_pay_cycle:{household_id}:{profile_id}"


def _day_key(household_id: int, profile_id: int) -> str:
    return f"budget_pay_cycle_day:{household_id}:{profile_id}"


def _monthly_rule(employment: Employment) -> PaydayRule | None:
    if employment.payday_rule == "last_friday":
        return PaydayRule(RuleKind.nth_weekday, weekday=4, nth=-1,
                          adjust=ShiftPolicy.none)
    if not employment.payday_rule:
        return None
    try:
        rule = parse_rule(employment.payday_rule)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return None if rule.kind in (RuleKind.weekly, RuleKind.nightly) else rule


def _employments(session: Session, household_id: int,
                 profile_id: int) -> list[Employment]:
    with session.no_autoflush:
        return list(session.scalars(select(Employment).where(
            Employment.household_id == household_id,
            Employment.profile_id == profile_id).order_by(Employment.id)))


def source_choices(session: Session, household_id: int,
                   profile_id: int) -> list[SourceChoice]:
    """Only this member's monthly sources, including retained history."""
    return [SourceChoice(employment_id=e.id, employer=e.employer)
            for e in _employments(session, household_id, profile_id)
            if _monthly_rule(e) is not None]


def selected_anchor(session: Session, household_id: int,
                    profile_id: int) -> int | None:
    with session.no_autoflush:
        row = session.get(AppMeta, _anchor_key(household_id, profile_id))
    if row is None:
        return None
    try:
        return int(row.value)
    except ValueError:
        return None


def selected_day(session: Session, household_id: int,
                 profile_id: int) -> int | None:
    with session.no_autoflush:
        row = session.get(AppMeta, _day_key(household_id, profile_id))
    if row is None:
        return None
    try:
        day = int(row.value)
    except ValueError:
        return None
    return day if 1 <= day <= 31 else None


def _validate_profile(session: Session, household_id: int, profile_id: int) -> None:
    profile = session.get(UserProfile, profile_id)
    if profile is None or profile.household_id != household_id:
        raise ValueError("Budget member does not belong to this household")


def _write_setting(session: Session, key: str, value: int | None) -> None:
    row = session.get(AppMeta, key)
    if value is None:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(AppMeta(key=key, value=str(value)))
    else:
        row.value = str(value)


def set_day(session: Session, household_id: int, profile_id: int,
            day: int | None) -> None:
    """Set a planned payday without requiring employment or salary records."""
    with session.no_autoflush:
        _validate_profile(session, household_id, profile_id)
        if day is not None and (type(day) is not int or not 1 <= day <= 31):
            raise ValueError("Payday day must be an integer between 1 and 31")
        _write_setting(session, _day_key(household_id, profile_id), day)
        _write_setting(session, _anchor_key(household_id, profile_id), None)
    session.flush()


def set_anchor(session: Session, household_id: int, profile_id: int,
               employment_id: int | None) -> None:
    """Persist explicit configuration; callers enforce write RBAC and audit."""
    with session.no_autoflush:
        _validate_profile(session, household_id, profile_id)
        if employment_id is not None:
            employment = session.get(Employment, employment_id)
            if (employment is None or employment.household_id != household_id
                    or employment.profile_id != profile_id
                    or _monthly_rule(employment) is None):
                raise ValueError("Select this member's monthly employment source")
        _write_setting(session, _anchor_key(household_id, profile_id), employment_id)
        _write_setting(session, _day_key(household_id, profile_id), None)
    session.flush()


def _start(employment: Employment) -> date | None:
    if employment.start_date is not None:
        return employment.start_date
    return min((r.effective_date for r in employment.salary_revisions),
               default=None)


def _covers(employment: Employment, start: date, end: date) -> bool:
    began = _start(employment)
    return ((began is None or began <= end)
            and (employment.end_date is None or employment.end_date >= start))


def _legacy_source(employment: Employment, end: date) -> bool:
    began = _start(employment)
    return (employment.is_active and _covers(employment, date.today(), date.today())
            and (began is None or end < began))


def _boundary(sources: list[Employment], year: int, month: int,
              selected: int | None) -> list[tuple[date, int]]:
    paydays = []
    for employment in sources:
        rule = _monthly_rule(employment)
        assert rule is not None
        paydays.append((employment, pay_date(rule, year, month)))
    if selected is not None:
        chosen = [(e, day) for e, day in paydays if e.id == selected]
        if not chosen:
            return []
        employment, day = chosen[0]
        if _covers(employment, day, day):
            return [(day, employment.id)]
    candidates = [(day, e.id) for e, day in paydays if _covers(e, day, day)]
    if not candidates:
        # A sole current rule can interpret legacy plans predating its record.
        candidates = [(day, e.id) for e, day in paydays
                      if (selected is None or e.id == selected) and _legacy_source(e, day)]
    return candidates


def resolve_period(session: Session, budget: Budget, *,
                   basis: str = "calendar") -> BudgetWindow:
    """Derive boundaries from planned paydays; never update stored plan data."""
    calendar = BudgetWindow(budget.start_date, budget.end_date, "calendar")
    if basis not in {"calendar", "pay_cycle"}:
        raise ValueError("Unknown budget period basis")
    if (basis == "calendar" or budget.owner_profile_id is None
            or budget.period_type != BudgetPeriodType.monthly):
        return calendar
    with session.no_autoflush:
        year, month = budget.start_date.year, budget.start_date.month
        previous_year = year - 1 if month == 1 else year
        previous_month = month - 1 or 12
        manual = session.get(AppMeta, _day_key(budget.household_id, budget.owner_profile_id))
        if manual is not None:
            day = selected_day(session, budget.household_id, budget.owner_profile_id)
            if day is None:
                return BudgetWindow(calendar.start, calendar.end, "calendar", warning=(
                    "Invalid configured payday day; showing calendar dates."))
            rule = PaydayRule(RuleKind.fixed_day, day=day, adjust=ShiftPolicy.previous)
            return BudgetWindow(pay_date(rule, previous_year, previous_month),
                                pay_date(rule, year, month) - timedelta(days=1), "pay_cycle")
        sources = [e for e in _employments(
            session, budget.household_id, budget.owner_profile_id)
            if _monthly_rule(e) is not None]
        selected = selected_anchor(session, budget.household_id,
                                   budget.owner_profile_id)
        opening = _boundary(sources, previous_year, previous_month, selected)
        closing = _boundary(sources, year, month, selected)
        if len(opening) != 1 or len(closing) != 1:
            warning = ("Multiple monthly employment sources; select a pay-cycle source. "
                       if len(opening) > 1 or len(closing) > 1 else
                       "No valid monthly employment source; configure a pay-cycle source. ")
            return BudgetWindow(calendar.start, calendar.end, "calendar",
                                warning=warning + "Showing calendar dates.")
        return BudgetWindow(
            opening[0][0], closing[0][0] - timedelta(days=1),
            "pay_cycle", opening[0][1])
