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
from contextlib import ExitStack
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import banks as bankdom
from app.domain import deliver as deliverdom
from app.domain import discovery as discdom
from app.domain import financial_events, settle
from app.domain import notifications as notifydom
from app.domain import sync as syncdom
from app.domain.sync_lock import claim
from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    ConnStatus,
    Household,
    MappingStatus,
    NotificationRule,
    SyncCursor,
    SyncStatus,
    TransactionSource,
)

if TYPE_CHECKING:
    from app.domain.deliver import HaNotifier
    from app.domain.discovery import BankClient

log = logging.getLogger(__name__)

TokenResolver = Callable[[Session, BankConnection], str]
Fetch = Callable[[SyncCursor], Iterator[syncdom.FeedPage]]


def _starling_fetch(client: BankClient, token: str,
                    mapping: BankAccountMapping, *,
                    on_error: Callable[[int | None, str, float | None], object] | None = None,
                    ) -> Fetch:
    from app.integrations.starling import starling_fetch
    return starling_fetch(client, token, mapping.external_uid,
                          str(mapping.external_category_uid), on_error=on_error)


def _due(cursor: SyncCursor, now: datetime,
         interval_seconds: float) -> bool:
    """A cursor is due for a scheduled pull when its last good sync
    is older than the interval — never-synced is due too."""
    if cursor.retry_at is not None and cursor.retry_at > now:
        return False
    if cursor.last_success_at is None:
        return True
    elapsed = (now - cursor.last_success_at).total_seconds()
    return elapsed >= interval_seconds


def sync_tick(session: Session, *,
              client: BankClient,
              token_for: TokenResolver,
              interval_seconds: float = 240 * 60,
              now: datetime | None = None) -> int:
    with ExitStack() as claims:
        return _sync_tick(session, claims=claims, client=client, token_for=token_for,
                          interval_seconds=interval_seconds, now=now)


def _sync_tick(session: Session, *, claims: ExitStack,
              client: BankClient,
              token_for: TokenResolver,
              interval_seconds: float = 240 * 60,
              now: datetime | None = None) -> int:
    """Drain queued refreshes + run due scheduled syncs.

    Returns the number of sync runs performed. Failure on one
    mapping marks its run/cursor but never aborts the tick."""
    now = now or datetime.now()
    syncdom.emit_stale_transitions(session, now=now, interval_seconds=interval_seconds)
    ran = 0
    queued = {(c.account_id, c.source): c
              for c in syncdom.drain_due(session, now)}
    # Release the writer before any provider I/O (#460): drain_due
    # flipped cursor flags — holding that write open across Starling
    # calls stalls every other writer to busy_timeout.
    session.commit()
    mappings = session.scalars(select(BankAccountMapping).where(
        BankAccountMapping.status == MappingStatus.linked,
        BankAccountMapping.account_id.is_not(None),
        BankAccountMapping.external_category_uid != "")).all()
    conns = {c.id: c for c in session.scalars(select(BankConnection).where(
        BankConnection.status.in_(
            [ConnStatus.connected, ConnStatus.degraded])))}
    backing_off = set(session.scalars(select(BankAccountMapping.connection_id).join(
        SyncCursor, SyncCursor.account_id == BankAccountMapping.account_id).where(
            BankAccountMapping.status == MappingStatus.linked,
            SyncCursor.source == TransactionSource.starling,
            SyncCursor.retry_at > now)))
    ownership: dict[int, bool] = {}
    completed_accounts: dict[int, set[int]] = {}
    tokens: dict[int, str] = {}
    metadata_succeeded: set[int] = set()
    refreshed_conns: set[int] = set()
    refresh_succeeded: set[int] = set()
    cursors_by_conn: dict[int, list[SyncCursor]] = {}
    denied_spaces_by_conn: dict[int, set[str]] = {}
    synced_households: set[int] = set()
    for m in mappings:
        conn = conns.get(m.connection_id)
        if conn is None or m.account_id is None \
                or not bankdom.syncable(conn):
            continue
        if bankdom.connection_retry_at(session, conn, now=now) is not None:
            continue
        cursor = syncdom.cursor_for(session, m.account_id,
                                    TransactionSource.starling)
        cursors_by_conn.setdefault(conn.id, []).append(cursor)
        refreshed = (m.account_id, TransactionSource.starling) in queued
        if not refreshed and not _due(cursor, now, interval_seconds):
            continue
        if conn.id not in ownership:
            ownership[conn.id] = claims.enter_context(
                claim(session, f"connection:{conn.id}"))
        if not ownership[conn.id]:
            cursor.refresh_requested = True
            session.commit()
            continue
        try:
            token = token_for(session, conn)
        except Exception as e:
            conn.last_error = str(e)[:500]
            refresh_succeeded.discard(conn.id)
            continue
        tokens[conn.id] = token
        if conn.id not in refreshed_conns and conn.id not in backing_off:
            # Scheduled parity with the manual /sync route (#416):
            # provider discovery + balance refresh — account
            # renames/closures and live balances used to propagate
            # only when a human pressed Sync.
            try:
                discdom.discover(session, conn, client, token)
                discdom.sync_balances(session, conn, client, token, reconcile_opening=False)
                metadata_succeeded.add(conn.id)
                denied_spaces_by_conn[conn.id] = discdom.unavailable_spaces(client)
                if denied_spaces_by_conn[conn.id]:
                    discdom.defer_unavailable_spaces(
                        session, conn, denied_spaces_by_conn[conn.id], interval_seconds, now=now)
                if not denied_spaces_by_conn[conn.id]:
                    refresh_succeeded.add(conn.id)
            except Exception as e:
                bankdom.record_api_error(
                    session, conn,
                    401 if isinstance(e, bankdom.TokenInvalid) else getattr(e, "status", None),
                    str(e), getattr(e, "retry_after", None))
            session.commit()   # writer window ends before next I/O
            refreshed_conns.add(conn.id)
        if not bankdom.syncable(conn):
            continue
        if cursor.retry_at is not None and cursor.retry_at > now:
            continue
        if m.status is not MappingStatus.linked:
            continue   # discovery just closed/unlinked it provider-side
        if (m.external_kind == "space"
                and m.external_uid in denied_spaces_by_conn.get(conn.id, set())):
            continue
        account = session.get(Account, m.account_id)
        if account is None:
            continue
        cursor.refresh_requested = False
        try:
            run = syncdom.sync_account(
                session, account, TransactionSource.starling,
                _starling_fetch(client, token, m,
                                on_error=partial(bankdom.record_api_error, session, conn)))
        except syncdom.SyncBusy:
            cursor.refresh_requested = True
            session.commit()
            refresh_succeeded.discard(conn.id)
            continue
        if run.status == SyncStatus.completed:
            completed_accounts.setdefault(conn.id, set()).add(account.id)
        synced_households.add(account.household_id)
        ran += 1
    for cid, account_ids in completed_accounts.items():
        conn = conns[cid]
        if (cid not in metadata_succeeded or not bankdom.syncable(conn)
                or bankdom.connection_retry_at(session, conn, now=now) is not None):
            continue
        try:
            discdom.reconcile_synced_balances(
                session, conn, client, tokens[cid], account_ids, interval_seconds=interval_seconds)
            if discdom.unavailable_spaces(client):
                refresh_succeeded.discard(cid)
        except Exception as exc:
            bankdom.record_api_error(
                session, conn,
                401 if isinstance(exc, bankdom.TokenInvalid) else getattr(exc, "status", None),
                str(exc), getattr(exc, "retry_after", None))
            refresh_succeeded.discard(cid)
        session.commit()
    for cid in refresh_succeeded:
        if all(c.status == SyncStatus.completed for c in cursors_by_conn[cid]):
            conns[cid].status = ConnStatus.connected
            conns[cid].last_error = None
    # Post-ingest settlement (issue #429): every sync trigger drains
    # through here — scheduled, queued manual refresh and webhook-
    # drained alike. Settlement failure never fails the sync itself.
    for hid in synced_households:
        try:
            stats = settle.post_sync(session, hid)
            if stats.matched or stats.queued:
                log.info("post-sync household %d: %s", hid, stats)
        except Exception:
            log.exception(
                "post-sync settlement failed for household %d", hid)
        session.commit()   # keep each household's settle self-contained
    return ran


def notify_tick(session: Session, *,
                notifier: HaNotifier | None,
                now: datetime | None = None) -> tuple[int, int]:
    """Evaluate every enabled rule, dispatch due events, deliver
    pending deliveries (issue #70 — the notification half of the
    job runner). Returns ``(raised, delivered)``."""
    now = now or datetime.now()
    for household_id in session.scalars(select(Household.id)).all():
        financial_events.evaluate_household(session, household_id, today=now.date())
    raised = 0
    rules = session.scalars(select(NotificationRule).where(
        NotificationRule.enabled.is_(True))).all()
    household_ids = {r.household_id for r in rules}
    for r in rules:
        try:
            raised += notifydom.evaluate(session, r, at=now)
        except Exception:
            log.exception("notification rule %d evaluation failed", r.id)
    # Evaluate/dispatch are pure compute — persist them before the
    # delivery phase, which makes HA notify HTTP calls; each delivery
    # commits as it lands so no writer spans the network (#460).
    session.commit()
    delivered = 0
    for hid in household_ids:
        notifydom.dispatch(session, hid, now=now)
        session.commit()
        if notifier is not None:
            delivered += deliverdom.deliver_pending(
                session, hid, notifier, now=now).sent
    return raised, delivered
