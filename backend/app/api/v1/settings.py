"""In-app banking & synchronisation settings (issue #431).

``GET /settings/banking`` — any active profile can read the effective
sync cadence and per-connection status.

``PATCH /settings/banking`` — admin only; persists the chosen
interval in ``app_meta`` where the running scheduler picks it up on
its next wake (no restart, no env-file edits). An enforced deploy
override (``FF_SYNC_INTERVAL_MINUTES`` / add-on options) wins over
the stored value and makes the control read-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import (
    ActorDep,
    HouseholdDep,
    ProfileDep,
    SessionDep,
    require_admin,
)
from app.domain import appconfig, banks
from app.persistence.models import (
    BankAccountMapping,
    BankConnection,
    ConnStatus,
    MappingStatus,
    SyncCursor,
    SyncRun,
    TransactionSource,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _next_due(cursor: SyncCursor | None, now: datetime,
              interval_seconds: float) -> datetime | None:
    """Mirror of the scheduler's due rule: queued refresh and active
    backoff outrank the plain cadence estimate."""
    if cursor is None:
        return now
    if cursor.retry_at is not None and cursor.retry_at > now:
        return cursor.retry_at
    if cursor.last_success_at is None or cursor.refresh_requested:
        return now
    return cursor.last_success_at + timedelta(seconds=interval_seconds)


def _status(request: Request, session: Session, household_id: int,
            ) -> schemas.BankingSyncSettingsOut:
    settings = request.app.state.settings
    interval, source = appconfig.effective_sync_interval_minutes(
        session, settings)
    now = datetime.now()
    interval_seconds = interval * 60
    scheduler_enabled = bool(getattr(request.app.state, "scheduler_enabled",
                                     settings.scheduler_tick_seconds > 0))

    conns = session.scalars(select(BankConnection).where(
        BankConnection.household_id == household_id)).all()
    mappings = session.scalars(select(BankAccountMapping).where(
        BankAccountMapping.household_id == household_id,
        BankAccountMapping.status == MappingStatus.linked)).all()
    by_conn: dict[int, list[BankAccountMapping]] = {}
    for m in mappings:
        by_conn.setdefault(m.connection_id, []).append(m)

    statuses: list[schemas.ConnectionSyncStatusOut] = []
    for c in conns:
        connection_retry = banks.connection_retry_at(session, c, now=now)
        mapped = by_conn.get(c.id)
        linked: list[BankAccountMapping | None] = (
            list(mapped) if mapped else [None])
        for lm in linked:
            cursor = (
                session.scalar(select(SyncCursor).where(
                    SyncCursor.account_id == lm.account_id,
                    SyncCursor.source == TransactionSource.starling))
                if lm is not None and lm.account_id is not None else None)
            retry_at = cursor.retry_at if cursor else None
            if connection_retry is not None:
                retry_at = max(retry_at, connection_retry) if retry_at else connection_retry
            next_due = _next_due(cursor, now, interval_seconds) if lm is not None else None
            if connection_retry is not None:
                next_due = max(next_due, connection_retry) if next_due else connection_retry
            last_attempt = (
                session.scalar(select(func.max(SyncRun.started_at)).where(
                    SyncRun.account_id == lm.account_id,
                    SyncRun.source == TransactionSource.starling))
                if lm is not None and lm.account_id is not None else None)
            statuses.append(schemas.ConnectionSyncStatusOut(
                connection_id=c.id,
                provider=c.provider.value,
                status=c.status.value,
                last_error=c.last_error,
                sync_status=cursor.status.value if cursor else None,
                sync_error=cursor.error if cursor else None,
                account_id=lm.account_id if lm is not None else None,
                last_attempted_at=last_attempt,
                last_success_at=(
                    cursor.last_success_at if cursor else None),
                refresh_queued=(
                    cursor.refresh_requested if cursor else False),
                retry_at=retry_at,
                next_due_at=next_due if scheduler_enabled and c.status in (
                    ConnStatus.connected, ConnStatus.degraded) else None))
    return schemas.BankingSyncSettingsOut(
        interval_minutes=interval,
        source=source,
        editable=source != "environment",
        min_minutes=appconfig.SYNC_INTERVAL_MIN_MINUTES,
        max_minutes=appconfig.SYNC_INTERVAL_MAX_MINUTES,
        tick_seconds=settings.scheduler_tick_seconds,
        scheduler_enabled=scheduler_enabled,
        connections=statuses)


@router.get("/banking", response_model=schemas.BankingSyncSettingsOut)
def banking_settings(request: Request, session: SessionDep,
                     household: HouseholdDep,
                     _profile: ProfileDep) -> schemas.BankingSyncSettingsOut:
    return _status(request, session, household.id)


@router.patch("/banking",
              response_model=schemas.BankingSyncSettingsOut,
              dependencies=[Depends(require_admin)])
def update_banking_settings(
        request: Request, session: SessionDep,
        household: HouseholdDep, actor: ActorDep,
        payload: schemas.BankingSyncSettingsPatch,
        ) -> schemas.BankingSyncSettingsOut:
    settings = request.app.state.settings
    if "sync_interval_minutes" in settings.model_fields_set:
        raise HTTPException(
            409,
            "sync interval is enforced by deployment configuration "
            "(FF_SYNC_INTERVAL_MINUTES / add-on options) — in-app "
            "changes are disabled")
    previous = appconfig.parse_interval(
        appconfig.get_value(session, appconfig.SYNC_INTERVAL_KEY))
    appconfig.set_value(session, appconfig.SYNC_INTERVAL_KEY,
                        str(payload.sync_interval_minutes))
    emit(session, household.id, actor, "settings.banking.update",
         "app_meta", 0, {
             "sync_interval_minutes": {
                 "old": previous, "new": payload.sync_interval_minutes}})
    session.commit()
    return _status(request, session, household.id)
