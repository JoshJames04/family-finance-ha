"""Persisted application configuration (issue #431).

Admin-editable runtime settings live in ``app_meta`` (the platform
KV table) so they survive restarts, upgrades and backup/restore.

Precedence for each configurable value, highest first:

1. An **enforced deploy-time override** — an ``FF_*`` env var or
   add-on ``options.json`` entry that explicitly set the field
   (detected via ``Settings.model_fields_set``). When present the
   in-app control is read-only and the UI explains why.
2. The **persisted UI value** in ``app_meta``.
3. The field's coded **default**.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.persistence.models import AppMeta

SYNC_INTERVAL_KEY = "sync_interval_minutes"

# Starling PAT rate limits comfortably allow ~1000 requests/day per
# token; the floor/ceiling keep polling inside that envelope even for
# several linked connections (#431).
SYNC_INTERVAL_MIN_MINUTES = 15.0
SYNC_INTERVAL_MAX_MINUTES = 1440.0


def get_value(session: Session, key: str) -> str | None:
    row = session.get(AppMeta, key)
    return row.value if row is not None else None


def set_value(session: Session, key: str, value: str) -> AppMeta:
    row = session.get(AppMeta, key)
    if row is None:
        row = AppMeta(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    session.flush()
    return row


def clear_value(session: Session, key: str) -> None:
    row = session.get(AppMeta, key)
    if row is not None:
        session.delete(row)
        session.flush()


def parse_interval(raw: str | None) -> float | None:
    """A stored interval must be a finite number inside the allowed
    bounds; anything else is treated as unset (never trusts a
    hand-edited/legacy row)."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not (SYNC_INTERVAL_MIN_MINUTES <= value
            <= SYNC_INTERVAL_MAX_MINUTES):
        return None
    return value


def effective_sync_interval_minutes(
    session: Session, settings: Settings,
) -> tuple[float, str]:
    """Resolve the running bank-sync cadence.

    Returns ``(minutes, source)`` where source is ``"environment"``
    (enforced deploy override), ``"app"`` (persisted admin choice) or
    ``"default"``.
    """
    if "sync_interval_minutes" in settings.model_fields_set:
        return settings.sync_interval_minutes, "environment"
    stored = parse_interval(get_value(session, SYNC_INTERVAL_KEY))
    if stored is not None:
        return stored, "app"
    return settings.sync_interval_minutes, "default"
