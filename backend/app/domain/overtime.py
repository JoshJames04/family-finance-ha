"""Overtime entries (issue #79).

Status flow: draft → submitted → approved (or rejected). Multiplier
defaults live on the employment's ``overtime_multipliers`` JSON —
never universal constants. The hourly rate is snapshotted at entry
creation from the salary revision effective on the work date, so a
later pay rise can't silently re-rate approved overtime.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import EllipsisType
from typing import Any

from sqlalchemy import select, update
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
    created_at = datetime.now(UTC).replace(tzinfo=None)
    e = OvertimeEntry(
        employment_id=employment.id, work_date=work_date, hours=hours,
        multiplier_bps=mult, day_type=dt, hourly_rate_pence=rate,
        notes=notes, created_at=created_at, updated_at=created_at)
    session.add(e)
    session.flush()
    return e


def _revision_time(entry: OvertimeEntry) -> datetime:
    """Advance even if two mutations see the same clock value."""
    return max(datetime.now(UTC).replace(tzinfo=None),
               entry.updated_at + timedelta(microseconds=1))


def _transition(entry: OvertimeEntry, to: OvertimeStatus) -> None:
    if to not in _ALLOWED[entry.status]:
        raise PayrollError(
            f"overtime {entry.id}: cannot move {entry.status} → {to}")
    entry.status = to
    entry.updated_at = _revision_time(entry)


def submit(entry: OvertimeEntry) -> None:
    _transition(entry, OvertimeStatus.submitted)


def approve(entry: OvertimeEntry) -> None:
    _transition(entry, OvertimeStatus.approved)


def reject(entry: OvertimeEntry) -> None:
    _transition(entry, OvertimeStatus.rejected)


def rework(entry: OvertimeEntry) -> None:
    _transition(entry, OvertimeStatus.draft)


def lock_entries(session: Session, employment_id: int) -> None:
    """Reserve SQLite's writer before reading entries for a mutation.

    A no-op employment update keeps all values, including updated_at. Both
    weekly and individual actions use this reservation so a waiting edit sees
    the latest status and correctly returns a submitted entry to draft.
    """
    session.execute(update(Employment).where(Employment.id == employment_id).values(
        id=Employment.id, updated_at=Employment.updated_at).execution_options(
            synchronize_session=False))


def transition_week(
    session: Session, employment_id: int, week_start: date,
    action: str, revisions: dict[int, str],
) -> list[OvertimeEntry]:
    """Transition the complete eligible week from a confirmed content snapshot.

    SQLite serializes writers. Acquire that reservation *before* reading the
    eligible set, including when authorization has already performed SELECTs.
    The no-op update preserves every employment value, including updated_at.
    Conditional entry writes additionally protect the captured content/status.
    The caller owns the transaction, including its associated audit events.
    """
    target = {"submit": OvertimeStatus.submitted, "approve": OvertimeStatus.approved,
              "reject": OvertimeStatus.rejected}[action]
    source = OvertimeStatus.draft if action == "submit" else OvertimeStatus.submitted
    lock_entries(session, employment_id)
    entries = list(session.scalars(select(OvertimeEntry).where(
        OvertimeEntry.employment_id == employment_id,
        OvertimeEntry.work_date >= week_start,
        OvertimeEntry.work_date <= week_start + timedelta(days=6),
        OvertimeEntry.status == source).order_by(OvertimeEntry.id).execution_options(
            populate_existing=True)))
    if not entries or {entry.id: entry.revision for entry in entries} != revisions:
        raise PayrollError("overtime week changed; refresh and confirm again")
    for entry in entries:
        if target not in _ALLOWED[entry.status]:
            raise PayrollError("overtime week changed; refresh and confirm again")
        # Hours are protected by the locked revision. SQLite can retain 2.555
        # while Numeric(5, 2) loads 2.56, making SQL hours equality incorrect.
        updated_id = session.scalar(update(OvertimeEntry).where(
            OvertimeEntry.id == entry.id,
            OvertimeEntry.employment_id == employment_id,
            OvertimeEntry.work_date == entry.work_date,
            OvertimeEntry.multiplier_bps == entry.multiplier_bps,
            OvertimeEntry.day_type == entry.day_type,
            OvertimeEntry.status == source,
            OvertimeEntry.hourly_rate_pence == entry.hourly_rate_pence,
            OvertimeEntry.notes == entry.notes,
        ).values(status=target, updated_at=_revision_time(entry)).returning(
            OvertimeEntry.id).execution_options(synchronize_session=False))
        if updated_id != entry.id:
            raise PayrollError("overtime week changed; refresh and confirm again")
        session.refresh(entry)
    return entries


def update_entry(
    session: Session, entry: OvertimeEntry, employment: Employment, *,
    work_date: date | None = None,
    hours: Decimal | None = None,
    multiplier_bps: int | None = None,
    day_type: DayType | None = None,
    notes: str | EllipsisType | None = ...,
    holidays: HolidayTable | None = None,
) -> OvertimeEntry:
    """Edit an entry. Moving the work date re-derives the day type
    (unless pinned) and re-snapshots the hourly rate; changing the
    day type re-defaults the multiplier unless one is pinned.
    Editing a non-draft reverts it to draft so it re-enters the
    approval flow — approved figures stay stable until edited."""
    if hours is not None:
        if hours <= 0:
            raise PayrollError("overtime hours must be positive")
        entry.hours = hours
    date_moved = False
    if work_date is not None and work_date != entry.work_date:
        date_moved = True
        entry.work_date = work_date
        if day_type is None:
            entry.day_type = day_type_for(work_date, holidays)
        entry.hourly_rate_pence = hourly_rate_pence(
            salary_at(session, employment.id, work_date),
            Decimal(str(employment.contracted_hours)))
    if day_type is not None:
        entry.day_type = day_type
    if multiplier_bps is not None:
        if multiplier_bps <= 0:
            raise PayrollError("overtime multiplier must be positive")
        entry.multiplier_bps = multiplier_bps
    elif date_moved or day_type is not None:
        entry.multiplier_bps = multipliers(employment)[entry.day_type]
    if notes is not ...:
        entry.notes = notes
    if entry.status is not OvertimeStatus.draft:
        entry.status = OvertimeStatus.draft
    entry.updated_at = _revision_time(entry)
    session.flush()
    return entry


def delete_entry(session: Session, entry: OvertimeEntry) -> None:
    """Remove an entry entirely — any status. Payroll runs keep
    their snapshotted components, so deleting an approved entry
    only removes it from future totals, never rewrites history."""
    session.delete(entry)
    session.flush()


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
