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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain import contributions, outbox
from app.persistence.models import (
    Account,
    AuditEvent,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
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


def emit_lifecycle(session: Session, c: RecurringCommitment, event_type: str) -> None:
    """Write only record facts; notes and formula text never enter the event bus."""
    account = session.get(Account, c.account_id)
    outbox.emit(session, c.household_id, event_type,
                entity_kind="commitment", entity_id=c.id,
                payload={"_scope_owner_profile_id": account.owner_profile_id if account else None,
                         "name": c.name, "amount_pence": c.amount_pence,
                         "expected_date": c.next_date.isoformat(),
                         "frequency": c.frequency.value,
                         "status": "active" if c.active else "paused"})


def _rescheduled_matches(session: Session, c: RecurringCommitment) -> dict[date, ExpectedMovement]:
    """Recover the original date of a matched occurrence from its recorded reschedules."""
    rows = session.execute(select(
        ExpectedMovement, func.json_extract(AuditEvent.payload, "$.from"),
        func.json_extract(AuditEvent.payload, "$.to"),
    ).join(AuditEvent, AuditEvent.entity_id == ExpectedMovement.id).where(
        ExpectedMovement.household_id == c.household_id,
        ExpectedMovement.account_id == c.account_id,
        ExpectedMovement.source_type == ExpectedSource.commitment,
        ExpectedMovement.source_id == c.id,
        ExpectedMovement.id.in_(select(ExpectedMatch.movement_id)),
        AuditEvent.household_id == c.household_id, AuditEvent.entity_type == "expected_movement",
        AuditEvent.action == "reschedule", AuditEvent.created_at >= ExpectedMovement.created_at,
    ).order_by(AuditEvent.id.desc()))
    origins: dict[ExpectedMovement, date] = {}
    for movement, previous, target in rows:
        current = origins.get(movement, movement.expected_date)
        if current.isoformat() == target and previous:
            origins[movement] = date.fromisoformat(previous)
    return {original: movement for movement, original in origins.items()}


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
    # Matched and written-off occurrences survive schedule regeneration. Include historical
    # links even if a void/correction has reduced their effective value to zero.
    preserved = session.scalars(select(ExpectedMovement).where(
        ExpectedMovement.household_id == c.household_id,
        ExpectedMovement.source_type == ExpectedSource.commitment,
        ExpectedMovement.source_id == c.id, ExpectedMovement.account_id == c.account_id,
        ExpectedMovement.expected_date.between(c.next_date, horizon),
        or_(ExpectedMovement.dismissed_at.is_not(None),
            ExpectedMovement.id.in_(select(ExpectedMatch.movement_id))),
    ))
    moved = _rescheduled_matches(session, c)
    moved_ids = {m.id for m in moved.values()}
    by_date = {m.expected_date: m for m in preserved if m.id not in moved_ids}
    by_date.update(moved)
    paid = contributions.matched_totals(session, [m.id for m in by_date.values()])
    created: list[Transaction] = []
    latest: date | None = None
    anchor_dom = c.day_of_month if c.day_of_month is not None else c.next_date.day
    nominal_dates = set(occurrences(c, horizon))
    deferred_dates = {original for original, movement in moved.items()
                      if c.last_generated is not None and original <= c.last_generated
                      and movement.expected_date <= horizon and movement.dismissed_at is None
                      and paid[movement.id] < abs(movement.amount_pence)}
    for d in sorted(nominal_dates | deferred_dates):
        ref = f"rec:{c.id}:{d.isoformat()}"
        exists = session.execute(
            select(Transaction.id).where(
                Transaction.account_id == c.account_id,
                Transaction.source == TransactionSource.system,
                Transaction.external_ref == ref,
            )
        ).scalar_one_or_none()
        movement = by_date.get(d)
        amount = c.amount_pence or 0
        if movement is not None and movement.dismissed_at is not None:
            amount = 0
        elif movement is not None:
            remainder = max(0, abs(movement.amount_pence) - paid[movement.id])
            amount = remainder if movement.amount_pence > 0 else -remainder
        due = movement.expected_date if movement is not None else d
        if exists is None and due <= horizon and (movement is None or amount != 0):
            t = Transaction(
                account_id=c.account_id, date=due,
                amount_pence=amount,
                payee=c.name, category_id=c.category_id,
                status=TransactionStatus.pending,
                source=TransactionSource.system, external_ref=ref,
            )
            session.add(t)
            if movement is not None:
                session.flush()
                contributions.confirm(session, movement, t)
            created.append(t)
        if d in nominal_dates:
            c.last_generated = d
            c.generated_count += 1
            latest = d
    if latest is not None:
        c.next_date = advance(latest, c.frequency, anchor_dom)
        session.flush()
    return created
