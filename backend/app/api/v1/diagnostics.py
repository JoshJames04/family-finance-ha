"""Diagnostics bundle (issue #26).

``GET /diagnostics`` returns a sanitised snapshot for support and
self-debugging: versions, job cadence, per-feed sync state, DB
stats and recent failures. **Nothing financial crosses the wire** —
amounts, payees, refs and any digit runs in free text are redacted
before the response leaves the process (asserted by test).
"""
from __future__ import annotations

import re
from typing import Any, cast

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import __version__
from app.api.deps import ProfileDep, SessionDep
from app.persistence.base import Base
from app.persistence.migrate import current_revision
from app.persistence.models import (
    BankConnection,
    MergeCandidate,
    NotificationDelivery,
    SyncCursor,
    SyncRun,
    SyncStatus,
)

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

_DIGITS = re.compile(r"\d+")


def _sanitise(text: str | None) -> str | None:
    """Redact digit runs — kills amounts, refs, counts inside any
    free-text field — then trim whitespace."""
    if text is None:
        return None
    return _DIGITS.sub("#", text).strip() or None


def _table_counts(session: Session) -> dict[str, int]:
    """Row counts per table — counts carry no financial meaning."""
    counts: dict[str, int] = {}
    for name, table in Base.metadata.tables.items():
        counts[name] = int(session.scalar(
            select(func.count()).select_from(table)) or 0)
    return counts


@router.get("")
def diagnostics(session: SessionDep, profile: ProfileDep
                ) -> dict[str, Any]:
    """Sanitised support bundle — admin or any active member can
    pull it; the content itself is the privacy boundary."""
    settings_bundle = {
        "app": {"version": __version__},
        "database": {
            "alembic_revision": current_revision(
                cast(Engine, session.get_bind())),
            "table_rows": _table_counts(session)},
        "sync": [
            {"account_id": c.account_id,
             "source": c.source.value,
             "status": c.status.value,
             "error": _sanitise(c.error),
             "last_success_at": c.last_success_at,
             "retry_at": c.retry_at,
             "refresh_requested": c.refresh_requested}
            for c in session.scalars(
                select(SyncCursor).order_by(SyncCursor.id))],
        "bank_connections": [
            {"provider": c.provider.value,
             "status": c.status.value,
             "last_error": _sanitise(c.last_error),
             "last_validated_at": c.last_validated_at}
            for c in session.scalars(
                select(BankConnection).order_by(BankConnection.id))],
        "recent_failures": {
            "sync_runs": [
                {"account_id": r.account_id,
                 "source": r.source.value,
                 "error": _sanitise(r.error),
                 "started_at": r.started_at}
                for r in session.scalars(
                    select(SyncRun)
                    .where(SyncRun.status == SyncStatus.failed)
                    .order_by(SyncRun.id.desc()).limit(10))],
            "deliveries": [
                {"rule_id": d.rule_id,
                 "error": _sanitise(d.error)}
                for d in session.scalars(
                    select(NotificationDelivery)
                    .where(NotificationDelivery.status == "failed")
                    .order_by(NotificationDelivery.id.desc()).limit(10))],
            "merge_candidates_pending": int(session.scalar(
                select(func.count()).select_from(MergeCandidate)
                .where(MergeCandidate.status == "pending")) or 0)},
    }
    return settings_bundle
