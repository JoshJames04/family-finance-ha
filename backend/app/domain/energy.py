"""Energy cost estimation (issue #103): metered daily consumption
× effective-dated tariffs → estimated pence, always tagged
``estimate`` — never confused with the actual supplier bill.

Maths is integer-exact: unit rates are stored as millipence/kWh and
consumption as whole Wh, so a day's unit cost is
``wh × millipence ÷ 1_000_000`` pence, rounded half-up per day.
Standing charge is whole pence per day inside the window.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    Budget,
    BudgetLine,
    EnergyConfig,
    EnergyReading,
    EnergyTariff,
)


class EnergyError(Exception):
    """No usable tariff/readings → API maps to 409."""


@dataclass(frozen=True)
class DayCost:
    date: date
    wh: int
    unit_pence: int
    standing_pence: int
    tariff_id: int


@dataclass(frozen=True)
class Estimate:
    start: date
    end: date  # inclusive
    days: tuple[DayCost, ...]
    unit_pence: int
    standing_pence: int
    total_pence: int
    missing_days: int  # window days with no reading


def tariff_on(session: Session, household_id: int,
              day: date) -> EnergyTariff | None:
    """Latest tariff effective on ``day`` — effective-dated supersede."""
    return session.execute(
        select(EnergyTariff)
        .where(EnergyTariff.household_id == household_id,
               EnergyTariff.effective_from <= day)
        .order_by(EnergyTariff.effective_from.desc())
        .limit(1)).scalar_one_or_none()


def estimate(session: Session, household_id: int, start: date,
             end: date) -> Estimate:
    """Inclusive ``[start, end]`` estimate. Every day prices under the
    tariff effective that day — a mid-window rate change splits
    naturally. Days without a reading contribute standing charge only
    and count into ``missing_days``."""
    if end < start:
        raise EnergyError("end precedes start")
    rows = session.execute(
        select(EnergyReading)
        .where(EnergyReading.household_id == household_id,
               EnergyReading.date >= start, EnergyReading.date <= end)
    ).scalars().all()
    by_day = {r.date: r for r in rows}
    days: list[DayCost] = []
    unit_total = standing_total = missing = 0
    day = start
    while day <= end:
        t = tariff_on(session, household_id, day)
        if t is None:
            raise EnergyError(f"no tariff effective on {day}")
        r = by_day.get(day)
        wh = r.wh if r else 0
        if r is None:
            missing += 1
        unit = (wh * t.unit_rate_millipence + 500_000) // 1_000_000
        days.append(DayCost(date=day, wh=wh, unit_pence=unit,
                            standing_pence=t.standing_charge_pence,
                            tariff_id=t.id))
        unit_total += unit
        standing_total += t.standing_charge_pence
        day += timedelta(days=1)
    return Estimate(start=start, end=end, days=tuple(days),
                    unit_pence=unit_total,
                    standing_pence=standing_total,
                    total_pence=unit_total + standing_total,
                    missing_days=missing)


@dataclass(frozen=True)
class MonthReport:
    """Month-to-date estimate vs the energy budget with a month-end
    projection (issue #104).

    Projection method (documented, deterministic): average daily
    consumption over the days WITH a reading so far, priced at the
    current effective tariff, extrapolated over the remaining days;
    plus the standing charge for every remaining day. Past months are
    reported without projection (the window is complete).
    """
    month_start: date
    month_end: date
    through: date
    estimate: Estimate
    budget_pence: int | None
    variance_pence: int | None
    projected_total_pence: int | None
    projected_variance_pence: int | None
    avg_daily_wh: int | None


def month_report(session: Session, household_id: int,
                 as_of: date | None = None) -> MonthReport:
    as_of = as_of or date.today()
    month_start = as_of.replace(day=1)
    next_month = (month_start.replace(day=28)
                  + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    through = min(as_of, month_end)
    est = estimate(session, household_id, month_start, through)

    cfg = session.execute(
        select(EnergyConfig).where(
            EnergyConfig.household_id == household_id)
    ).scalar_one_or_none()
    budget_pence: int | None = None
    if cfg is not None and cfg.budget_category_id is not None:
        rows = session.execute(
            select(func.coalesce(
                func.sum(func.coalesce(BudgetLine.revised_pence,
                                       BudgetLine.original_pence)), 0))
            .join(Budget, BudgetLine.budget_id == Budget.id)
            .where(Budget.household_id == household_id,
                   BudgetLine.category_id == cfg.budget_category_id,
                   Budget.start_date <= month_end,
                   Budget.end_date >= month_start)).one()
        budget_pence = int(rows[0] or 0)

    metered = [d for d in est.days if d.wh > 0]
    avg_wh = (sum(d.wh for d in metered) // len(metered)
              if metered else None)
    projected_total: int | None = None
    if avg_wh is not None and through < month_end:
        remaining = (month_end - through).days
        t = tariff_on(session, household_id, month_end)
        assert t is not None  # estimate() already verified coverage
        future_unit = (avg_wh * t.unit_rate_millipence
                       + 500_000) // 1_000_000
        projected_total = (est.total_pence + remaining * future_unit
                           + remaining * t.standing_charge_pence)
    elif avg_wh is not None:
        projected_total = est.total_pence  # complete month
    return MonthReport(
        month_start=month_start, month_end=month_end, through=through,
        estimate=est, budget_pence=budget_pence,
        variance_pence=(est.total_pence - budget_pence
                        if budget_pence is not None else None),
        projected_total_pence=projected_total,
        projected_variance_pence=(
            projected_total - budget_pence
            if projected_total is not None
            and budget_pence is not None else None),
        avg_daily_wh=avg_wh)
