"""Issue #67 — incremental feed sync with dedup + resume.

Feed-agnostic: callers supply pages of ``FeedItem`` (external_ref,
date, amount, payee) — the Starling client plugs in later; tests
drive synthetic pages. Each page is committed as a unit and advances
the cursor, so a mid-feed failure leaves a resumable position and
never half-applies a page.

Dedup is two-layer:

1. **Exact** — ``(account_id, source, external_ref)`` is UNIQUE, so
   re-syncing an identical window inserts zero rows (AC 1).
2. **Fuzzy** — an unseen ref matching an existing row on
   (account, date, |amount|, casefolded payee) becomes a
   ``MergeCandidate`` for review — flagged, never auto-deleted, and
   crucially NOT inserted, so overlap with imported/manual history
   can't double-count (AC 2).
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import outbox
from app.persistence.models import (
    Account,
    MergeCandidate,
    MergeStatus,
    SyncCursor,
    SyncRun,
    SyncStatus,
    Transaction,
    TransactionSource,
    TransactionStatus,
)


class RateLimited(Exception):
    """Feed signalled backpressure — run fails resumable.
    ``retry_after`` (seconds, from the provider's Retry-After)
    defines the backoff window (issue #70)."""

    def __init__(self, message: str = "rate limited",
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SyncError(Exception):
    """Fatal feed failure — run fails resumable too; the cursor is
    the checkpoint either way."""


@dataclass(frozen=True)
class FeedItem:
    external_ref: str | None
    date: date
    amount_pence: int
    payee: str
    status: TransactionStatus | None = None
    provider_category: str | None = None


@dataclass(frozen=True)
class FeedPage:
    """One fetched batch. ``cursor_ref`` is the feed's own
    resumption token (e.g. Starling's changesSince watermark); when
    absent the cursor falls back to the last item's ref."""
    items: list[FeedItem]
    cursor_ref: str | None = None


def cursor_for(session: Session, account_id: int,
               source: TransactionSource) -> SyncCursor:
    c = session.scalar(
        select(SyncCursor).where(
            SyncCursor.account_id == account_id,
            SyncCursor.source == source))
    if c is None:
        c = SyncCursor(account_id=account_id, source=source)
        session.add(c)
        session.flush()
    return c


def _fuzzy_match(session: Session, account_id: int,
                 item: FeedItem) -> Transaction | None:
    """An existing row on same account/date/|amount| with matching
    payee (case-insensitive) and a DIFFERENT lineage — i.e. manual or
    imported history the feed is re-describing."""
    return session.scalar(
        select(Transaction)
        .where(Transaction.account_id == account_id,
               Transaction.date == item.date,
               Transaction.amount_pence == item.amount_pence,
               func.lower(Transaction.payee) == item.payee.lower(),
               Transaction.status != TransactionStatus.void)
        .order_by(Transaction.id)
        .limit(1))


def _ingest_item(session: Session, account: Account,
                 source: TransactionSource, item: FeedItem,
                 run: SyncRun) -> None:
    run.fetched += 1
    if item.external_ref is not None:
        dup = session.scalar(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.source == source,
                Transaction.external_ref == item.external_ref))
        if dup is not None:
            # changes-feed re-delivers mutated items — refresh the
            # mutable fields rather than pure-skip (spike #64:
            # PENDING -> SETTLED arrives as the same ref again)
            if item.status is not None and dup.status != item.status:
                dup.status = item.status
            if item.provider_category is not None \
                    and dup.provider_category != item.provider_category:
                dup.provider_category = item.provider_category
            run.deduped += 1
            return
    existing = _fuzzy_match(session, account.id, item)
    if existing is not None and existing.source != source:
        session.add(MergeCandidate(
            household_id=account.household_id, account_id=account.id,
            existing_txn_id=existing.id,
            incoming={"external_ref": item.external_ref,
                      "date": item.date.isoformat(),
                      "amount_pence": item.amount_pence,
                      "payee": item.payee}))
        run.merge_candidates += 1
        return
    txn = Transaction(
        account_id=account.id, date=item.date,
        amount_pence=item.amount_pence, payee=item.payee,
        status=item.status or TransactionStatus.cleared,
        source=source, external_ref=item.external_ref,
        provider_category=item.provider_category)
    session.add(txn)
    session.flush()
    outbox.emit(
        session, account.household_id, "transaction.imported",
        entity_kind="transaction", entity_id=txn.id,
        payload={
            "account_id": account.id,
            "date": item.date.isoformat(),
            "payee": item.payee,
            "amount_pence": item.amount_pence,
            "source": source.value},
        dedup_key=f"txn.imported:{txn.id}")
    run.inserted += 1


def sync_account(
    session: Session, account: Account,
    source: TransactionSource,
    fetch: Callable[[SyncCursor], Iterator[FeedPage]],
) -> SyncRun:
    """Pull pages from ``fetch`` until exhaustion or failure.

    ``fetch`` yields ``FeedPage``s; each completed page advances the
    cursor so a later resume continues where this run stopped (AC 3).
    ``RateLimited``/``SyncError`` mark the run failed — resumable."""
    cursor = cursor_for(session, account.id, source)
    was_failed = cursor.status == SyncStatus.failed
    run = SyncRun(account_id=account.id, source=source)
    session.add(run)
    # Commit, not flush: WAL permits one writer at a time, and the
    # page loop below holds network I/O inside the transaction —
    # an uncommitted writer held across provider fetches stalls
    # every other write request (busy_timeout) and, via the auth
    # middleware's profile commit path, could freeze the event
    # loop outright (#460). Each page commits as its own unit so
    # the writer window stays milliseconds, not a sync's duration.
    # A fetch may refresh expired cursor fields; leave nothing to autoflush.
    cursor.status = SyncStatus.running
    cursor.error = None
    session.commit()
    unexpected_error: Exception | None = None
    try:
        for page in fetch(cursor):
            from app.domain.financial_events import evaluate_budgets
            evaluate_budgets(session, account.household_id)
            for item in page.items:
                _ingest_item(session, account, source, item, run)
            if page.cursor_ref:
                cursor.last_ref = page.cursor_ref
            elif page.items:
                cursor.last_ref = page.items[-1].external_ref
            if page.items:
                cursor.last_date = page.items[-1].date
            evaluate_budgets(session, account.household_id)
            session.commit()         # page checkpoint — writer released
        run.status = SyncStatus.completed
        cursor.status = SyncStatus.completed
        cursor.last_success_at = datetime.now()
        cursor.retry_at = None
    except RateLimited as e:
        run.status = SyncStatus.failed
        run.error = str(e)[:500]
        cursor.status = SyncStatus.failed
        cursor.error = str(e)[:500]
        cursor.retry_at = (datetime.now()
                           + timedelta(seconds=e.retry_after or 60))
    except SyncError as e:
        run.status = SyncStatus.failed
        run.error = str(e)[:500]
        cursor.status = SyncStatus.failed
        cursor.error = str(e)[:500]
    except Exception as e:
        # Discard an incomplete page, including flushed rows and counters.
        # The run and previous page checkpoints were already committed.
        session.rollback()
        run.status = SyncStatus.failed
        cursor.status = SyncStatus.failed
        run.error = cursor.error = "unexpected sync failure"
        unexpected_error = e
    run.finished_at = datetime.now()
    outbox.emit(
        session, account.household_id,
        f"sync.{run.status.value}",
        entity_kind="account", entity_id=account.id,
        payload={
            "source": source.value, "status": run.status.value,
            "inserted": run.inserted, "deduped": run.deduped,
            "error_code": "sync_failed" if run.status == SyncStatus.failed else None},
        dedup_key=f"sync.run:{run.id}")
    if was_failed and run.status == SyncStatus.completed:
        outbox.emit(session, account.household_id, "sync.recovered", entity_kind="account",
                    entity_id=account.id, payload={"source": source.value, "status": "recovered"},
                    dedup_key=f"sync.recovered:{run.id}")
    session.commit()
    if unexpected_error is not None:
        raise unexpected_error
    return run


def request_refresh(session: Session, account_id: int,
                    source: TransactionSource,
                    now: datetime | None = None) -> bool:
    """Manual refresh (issue #70). During an active backoff window
    the request is *queued* on the cursor — the next drain runs it;
    outside backoff it returns False meaning "run now"."""
    now = now or datetime.now()
    c = cursor_for(session, account_id, source)
    if c.retry_at is not None and c.retry_at > now:
        c.refresh_requested = True
        session.flush()
        return True
    return False


def drain_due(session: Session,
              now: datetime | None = None) -> list[SyncCursor]:
    """Queued refreshes whose backoff expired (issue #70) plus
    cursors flagged refresh_requested — the job runner performs the
    actual ingest; this is the "what's due" query."""
    now = now or datetime.now()
    return list(session.scalars(select(SyncCursor).where(
        SyncCursor.refresh_requested.is_(True),
        (SyncCursor.retry_at.is_(None)
         | (SyncCursor.retry_at <= now)))))


def resolve_merge(session: Session, candidate: MergeCandidate,
                  action: str) -> None:
    """Reviewer decision: ``merged`` keeps the existing row (the
    incoming ref is annotated onto it for future exact dedup);
    ``dismissed`` drops the flag — the incoming row stays absent
    (it was never inserted, so nothing to undo)."""
    if action == "merged":
        existing = session.get(Transaction, candidate.existing_txn_id)
        ref = candidate.incoming.get("external_ref")
        if existing is not None and ref and not existing.external_ref:
            existing.external_ref = str(ref)
        candidate.status = MergeStatus.merged
    elif action == "dismissed":
        candidate.status = MergeStatus.dismissed
    else:
        raise SyncError(f"unknown merge action {action}")
    candidate.resolved_at = datetime.now()
    session.flush()


def emit_stale_transitions(session: Session, *, now: datetime,
                           interval_seconds: float) -> int:
    """One stale event per missed freshness episode, from real bank cursors.

    Freshness allows two configured sync intervals (at least five minutes).
    A never-synced cursor gets the same grace from its creation timestamp.
    """
    threshold = max(300.0, 2 * interval_seconds)
    count = 0
    rows = session.execute(select(SyncCursor, Account).join(
        Account, SyncCursor.account_id == Account.id).where(
            Account.archived.is_(False), SyncCursor.source == TransactionSource.starling))
    for cursor, account in rows:
        reference = cursor.last_success_at or cursor.created_at
        if reference is None or (now - reference).total_seconds() < threshold:
            continue
        event = outbox.emit(session, account.household_id, "data.stale",
                            entity_kind="account", entity_id=account.id,
                            payload={"source": cursor.source.value, "status": "stale",
                                     "date": reference.isoformat()},
                            dedup_key=f"data.stale:{cursor.id}:{reference.isoformat()}", at=now)
        count += event is not None
    return count
