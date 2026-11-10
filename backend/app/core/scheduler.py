"""Job runner for periodic background work (issue #70).

Two jobs, driven from the app lifespan:

- ``sync_tick`` — drains queued manual refreshes whose backoff
  window has passed and runs due scheduled syncs for linked
  bank mappings. Backoff is provider-driven (``retry_at`` from
  ``RateLimited``); a refresh requested inside the window stays
  queued, never bypasses.
- ``notify_tick`` — evaluates enabled notification rules,
  dispatches due events and delivers pending deliveries through
  the configured notifier.

Each job takes its dependencies explicitly so tests drive ticks
directly without waiting on a real clock; the scheduler loop
merely assembles deps from ``app.state`` and sleeps.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import banks as bankdom
from app.domain import deliver as deliverdom
from app.domain import discovery as discdom
from app.domain import notifications as notifydom
from app.domain import sync as syncdom
from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    ConnStatus,
    MappingStatus,
    NotificationRule,
    SyncCursor,
    TransactionSource,
)

if TYPE_CHECKING:
    from app.domain.deliver import HaNotifier
    from app.domain.discovery import BankClient

log = logging.getLogger(__name__)

TokenResolver = Callable[[Session, BankConnection], str]
Fetch = Callable[[SyncCursor], Iterator[syncdom.FeedPage]]


def _starling_fetch(client: BankClient, token: str,
                    mapping: BankAccountMapping) -> Fetch:
    from app.integrations.starling import starling_fetch
    return starling_fetch(client, token, mapping.external_uid,
                          str(mapping.external_category_uid))


def _due(cursor: SyncCursor, now: datetime,
         interval_seconds: float) -> bool:
    """A cursor is due for a scheduled pull when its last good sync
    is older than the interval — never-synced is due too."""
    if cursor.last_success_at is None:
        return True
    elapsed = (now - cursor.last_success_at).total_seconds()
    return elapsed >= interval_seconds


def sync_tick(session: Session, *,
              client: BankClient,
              token_for: TokenResolver,
              interval_seconds: float = 240 * 60,
              now: datetime | None = None) -> int:
    """Drain queued refreshes + run due scheduled syncs.

    Returns the number of sync runs performed. Failure on one
    mapping marks its run/cursor but never aborts the tick."""
    now = now or datetime.now()
    ran = 0
    queued = {(c.account_id, c.source): c
              for c in syncdom.drain_due(session, now)}
    mappings = session.scalars(select(BankAccountMapping).where(
        BankAccountMapping.status == MappingStatus.linked,
        BankAccountMapping.account_id.is_not(None),
        BankAccountMapping.external_category_uid != "")).all()
    conns = {c.id: c for c in session.scalars(select(BankConnection).where(
        BankConnection.status.in_(
            [ConnStatus.connected, ConnStatus.degraded])))}
    refreshed_conns: set[int] = set()
    for m in mappings:
        conn = conns.get(m.connection_id)
        if conn is None or m.account_id is None \
                or not bankdom.syncable(conn):
            continue
        cursor = syncdom.cursor_for(session, m.account_id,
                                    TransactionSource.starling)
        refreshed = (m.account_id, TransactionSource.starling) in queued
        if not refreshed and not _due(cursor, now, interval_seconds):
            continue
        try:
            token = token_for(session, conn)
        except Exception as e:
            conn.last_error = str(e)[:500]
            continue
        if conn.id not in refreshed_conns:
            # Scheduled parity with the manual /sync route (#416):
            # provider discovery + balance refresh — account
            # renames/closures and live balances used to propagate
            # only when a human pressed Sync.
            try:
                discdom.discover(session, conn, client, token)
                discdom.sync_balances(session, conn, client, token)
            except Exception as e:
                conn.last_error = str(e)[:500]
            refreshed_conns.add(conn.id)
        if m.status is not MappingStatus.linked:
            continue   # discovery just closed/unlinked it provider-side
        account = session.get(Account, m.account_id)
        if account is None:
            continue
        cursor.refresh_requested = False
        syncdom.sync_account(
            session, account, TransactionSource.starling,
            _starling_fetch(client, token, m))
        ran += 1
    session.flush()
    return ran


def notify_tick(session: Session, *,
                notifier: HaNotifier | None,
                now: datetime | None = None) -> tuple[int, int]:
    """Evaluate every enabled rule, dispatch due events, deliver
    pending deliveries (issue #70 — the notification half of the
    job runner). Returns ``(raised, delivered)``."""
    now = now or datetime.now()
    raised = 0
    rules = session.scalars(select(NotificationRule).where(
        NotificationRule.enabled.is_(True))).all()
    household_ids = {r.household_id for r in rules}
    for r in rules:
        try:
            raised += notifydom.evaluate(session, r, at=now)
        except Exception:
            log.exception("notification rule %d evaluation failed", r.id)
    delivered = 0
    for hid in household_ids:
        notifydom.dispatch(session, hid, now=now)
        if notifier is not None:
            delivered += deliverdom.deliver_pending(
                session, hid, notifier, now=now).sent
    session.flush()
    return raised, delivered
