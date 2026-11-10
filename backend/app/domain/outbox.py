"""Integration event outbox (issue #437 / ADR-0009).

The authoritative feed the Home Assistant integration drains:
writers call ``emit`` inside the same DB transaction as the state
change, so a committed event is never lost and a rolled-back change
never ghosts one. ``dedup_key`` gives once-per-entity transitions
idempotency across syncs, replays and restarts.

Payloads are written in full; ``feed`` redacts at read time by the
persisted ``ha_event_detail`` app setting — ``safe`` (default) keeps
structure/status/dates only; ``detailed`` (admin opt-in) adds names,
payees and pence amounts.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import AppMeta, IntegrationEvent

DETAIL_KEY = "ha_event_detail"

# Fields safe to expose on the HA event bus without opt-in —
# structure, status and dates; never names, payees or amounts.
_SAFE_KEYS = frozenset({
    "source", "source_type", "status", "old_status", "new_status",
    "state", "date", "expected_date", "occurrence_date", "direction",
    "count", "inserted", "deduped", "skipped", "currency", "manual",
    "frequency", "matched_pence_outstanding",
})


def detail_enabled(session: Session) -> bool:
    row = session.get(AppMeta, DETAIL_KEY)
    return row is not None and row.value == "detailed"


def set_detail(session: Session, mode: str) -> None:
    if mode not in ("safe", "detailed"):
        raise ValueError("ha_event_detail must be 'safe' or 'detailed'")
    row = session.get(AppMeta, DETAIL_KEY)
    if row is None:
        row = AppMeta(key=DETAIL_KEY, value=mode)
        session.add(row)
    else:
        row.value = mode
    session.flush()


def emit(session: Session, household_id: int, event_type: str, *,
         entity_kind: str | None = None, entity_id: int | None = None,
         payload: dict[str, object] | None = None, dedup_key: str | None = None,
         at: datetime | None = None) -> IntegrationEvent | None:
    """Append an outbox row. A ``dedup_key`` already seen returns
    None — the transition fires once, period."""
    if dedup_key is not None:
        seen = session.execute(
            select(IntegrationEvent.id).where(
                IntegrationEvent.dedup_key == dedup_key)
        ).scalar_one_or_none()
        if seen is not None:
            return None
    row = IntegrationEvent(
        household_id=household_id, event_type=event_type,
        entity_kind=entity_kind, entity_id=entity_id,
        occurred_at=at or datetime.now(), payload=payload or {},
        dedup_key=dedup_key)
    session.add(row)
    session.flush()
    return row


def feed(session: Session, household_id: int, *,
         after: int = 0, limit: int = 100,
         ) -> tuple[list[IntegrationEvent], int]:
    """Ordered drain from cursor ``after``; returns (rows, cursor)."""
    rows = list(session.scalars(
        select(IntegrationEvent).where(
            IntegrationEvent.household_id == household_id,
            IntegrationEvent.id > after)
        .order_by(IntegrationEvent.id).limit(limit)).all())
    cursor = rows[-1].id if rows else after
    return rows, cursor


def visible_payload(event: IntegrationEvent, detailed: bool) -> dict[str, object]:
    """Read-time redaction — safe mode strips anything that could
    name or value a real record (ADR-0009)."""
    if detailed:
        return dict(event.payload)
    return {k: v for k, v in event.payload.items() if k in _SAFE_KEYS}
