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
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
    """Feed signalled backpressure — run fails resumable."""


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
    session.add(Transaction(
        account_id=account.id, date=item.date,
        amount_pence=item.amount_pence, payee=item.payee,
        status=item.status or TransactionStatus.cleared,
        source=source, external_ref=item.external_ref))
    session.flush()
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
    run = SyncRun(account_id=account.id, source=source)
    session.add(run)
    session.flush()
    cursor.status = SyncStatus.running
    cursor.error = None
    try:
        for page in fetch(cursor):
            for item in page.items:
                _ingest_item(session, account, source, item, run)
            if page.cursor_ref:
                cursor.last_ref = page.cursor_ref
            elif page.items:
                cursor.last_ref = page.items[-1].external_ref
            if page.items:
                cursor.last_date = page.items[-1].date
            session.flush()          # page checkpoint
        run.status = SyncStatus.completed
        cursor.status = SyncStatus.completed
    except (RateLimited, SyncError) as e:
        run.status = SyncStatus.failed
        run.error = str(e)[:500]
        cursor.status = SyncStatus.failed
        cursor.error = str(e)[:500]
    run.finished_at = datetime.now()
    session.flush()
    return run


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
