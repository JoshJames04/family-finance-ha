"""Overtime entries (issue #79).

Status flow: draft → submitted → approved (or rejected). Multiplier
defaults live on the employment's ``overtime_multipliers`` JSON —
never universal constants. The hourly rate is snapshotted at entry
creation from the salary revision effective on the work date, so a
later pay rise can't silently re-rate approved overtime.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.paydays import HolidayTable, load_holidays
from app.domain.payroll import PayrollError, hourly_rate_pence, salary_at
from app.persistence.models import (
    DayType,
    Employment,
    OvertimeEntry,
    OvertimeStatus,
)

# Fallback only when an employment hasn't configured its own rates —
# configurable per employment via ``overtime_multipliers``.
FALLBACK_MULTIPLIERS: dict[DayType, int] = {
    DayType.weekday: 15_000,
    DayType.saturday: 15_000,
    DayType.sunday: 20_000,
    DayType.bank_holiday: 20_000,
}

_ALLOWED: dict[OvertimeStatus, set[OvertimeStatus]] = {
    OvertimeStatus.draft: {OvertimeStatus.submitted},
    OvertimeStatus.submitted: {OvertimeStatus.approved,
                               OvertimeStatus.rejected},
    OvertimeStatus.approved: set(),
    OvertimeStatus.rejected: {OvertimeStatus.draft},  # rework
}


def multipliers(employment: Employment) -> dict[DayType, int]:
    """Employment-configured multipliers (bps) over the fallback."""
    raw: dict[str, Any] = json.loads(
        employment.overtime_multipliers or "{}")
    out = dict(FALLBACK_MULTIPLIERS)
    for k, v in raw.items():
        out[DayType(k)] = int(v)
    return out


def day_type_for(work_date: date,
                 holidays: HolidayTable | None = None) -> DayType:
    hol = holidays or load_holidays()
    if hol.is_holiday(work_date):
        return DayType.bank_holiday
    return {5: DayType.saturday, 6: DayType.sunday}.get(
        work_date.weekday(), DayType.weekday)


def entry_pay_pence(entry: OvertimeEntry) -> int:
    """hours × snapshotted hourly rate × multiplier, floored to pence."""
    return entry.pay_pence


def create_entry(
    session: Session, employment: Employment, *,
    work_date: date, hours: Decimal,
    multiplier_bps: int | None = None,
    day_type: DayType | None = None,
    notes: str | None = None,
    holidays: HolidayTable | None = None,
) -> OvertimeEntry:
    """New draft entry; resolves day type + multiplier, snapshots the
    hourly rate effective on the work date."""
    if hours <= 0:
        raise PayrollError("overtime hours must be positive")
    dt = day_type or day_type_for(work_date, holidays)
    mult = (multiplier_bps if multiplier_bps is not None
            else multipliers(employment)[dt])
    if mult <= 0:
        raise PayrollError("overtime multiplier must be positive")
    rate = hourly_rate_pence(
        salary_at(session, employment.id, work_date),
        Decimal(str(employment.contracted_hours)))
    e = OvertimeEntry(
        employment_id=employment.id, work_date=work_date, hours=hours,
        multiplier_bps=mult, day_type=dt, hourly_rate_pence=rate,
        notes=notes)
    session.add(e)
    session.flush()
    return e


def _transition(entry: OvertimeEntry, to: OvertimeStatus) -> None:
    if to not in _ALLOWED[entry.status]:
        raise PayrollError(
            f"overtime {entry.id}: cannot move {entry.status} → {to}")
    entry.status = to


def submit(entry: OvertimeEntry) -> None:
    _transition(entry, OvertimeStatus.submitted)


def approve(entry: OvertimeEntry) -> None:
    _transition(entry, OvertimeStatus.approved)


def reject(entry: OvertimeEntry) -> None:
    _transition(entry, OvertimeStatus.rejected)


def approved_total_pence(
    session: Session, employment_id: int,
    start: date, end: date,
) -> int:
    """Approved entries only — the figure a payroll run may consume."""
    rows = session.execute(
        select(OvertimeEntry).where(
            OvertimeEntry.employment_id == employment_id,
            OvertimeEntry.status == OvertimeStatus.approved,
            OvertimeEntry.work_date >= start,
            OvertimeEntry.work_date <= end)
    ).scalars().all()
    return sum(entry_pay_pence(e) for e in rows)
