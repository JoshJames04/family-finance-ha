"""Recurring commitments: occurrence scheduling + idempotent materialise.

Schedule rules:
  weekly/fortnightly  — every 7/14 days from next_date
  monthly/quarterly   — same day-of-month (clamped to month end) 1/3 months on
  annually            — same month+day each year (29 Feb → 28 Feb non-leap)
End conditions: stop when next_date > end_date, or generated_count reaches
occurrence_count, whichever first.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    Frequency,
    RecurringCommitment,
    Transaction,
    TransactionSource,
    TransactionStatus,
)


def _clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def advance(d: date, freq: Frequency, day_of_month: int | None) -> date:
    dom = day_of_month if day_of_month is not None else d.day
    if freq == Frequency.weekly:
        return d + timedelta(days=7)
    if freq == Frequency.fortnightly:
        return d + timedelta(days=14)
    if freq == Frequency.monthly:
        y, m = (d.year, d.month + 1) if d.month < 12 else (d.year + 1, 1)
        return _clamp_day(y, m, dom)
    if freq == Frequency.quarterly:
        m = d.month + 3
        y = d.year + (m - 1) // 12
        return _clamp_day(y, (m - 1) % 12 + 1, dom)
    if freq == Frequency.annually:
        return _clamp_day(d.year + 1, d.month, d.day)
    raise ValueError(f"unknown frequency {freq}")


def occurrences(c: RecurringCommitment, horizon: date) -> Iterator[date]:
    """Occurrence dates from next_date up to and including horizon.

    The day-of-month anchor is explicit day_of_month, else the day of the
    current next_date — carried forward so '31st' survives short months
    (31 Jan → 28 Feb → 31 Mar), not drifting to 28/28/28.
    """
    count = c.generated_count or 0
    anchor_dom = c.day_of_month if c.day_of_month is not None else c.next_date.day
    d = c.next_date
    while d <= horizon:
        if c.end_date is not None and d > c.end_date:
            return
        if c.occurrence_count is not None and count >= c.occurrence_count:
            return
        yield d
        count += 1
        d = advance(d, c.frequency, anchor_dom)


def materialise(
    session: Session,
    c: RecurringCommitment,
    horizon: date,
) -> list[Transaction]:
    """Create pending transactions for occurrences up to horizon.

    Idempotent: each generated row carries external_ref
    ``rec:{commitment_id}:{date}`` so re-runs never duplicate.
    """
    if not c.active:
        return []
    if c.formula is not None:
        raise ValueError(
            "formula commitments resolve via payroll module, not "
            "materialise"
        )
    created: list[Transaction] = []
    latest: date | None = None
    anchor_dom = c.day_of_month if c.day_of_month is not None else c.next_date.day
    for d in occurrences(c, horizon):
        ref = f"rec:{c.id}:{d.isoformat()}"
        exists = session.execute(
            select(Transaction.id).where(
                Transaction.account_id == c.account_id,
                Transaction.source == TransactionSource.system,
                Transaction.external_ref == ref,
            )
        ).scalar_one_or_none()
        if exists is None:
            t = Transaction(
                account_id=c.account_id, date=d,
                amount_pence=c.amount_pence or 0,
                payee=c.name, category_id=c.category_id,
                status=TransactionStatus.pending,
                source=TransactionSource.system, external_ref=ref,
            )
            session.add(t)
            created.append(t)
        c.last_generated = d
        c.generated_count += 1
        latest = d
    if latest is not None:
        c.next_date = advance(latest, c.frequency, anchor_dom)
        session.flush()
    return created
