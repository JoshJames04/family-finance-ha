"""Occurrence settlement (issue #429): the canonical "paid" writes.

A reliable bank→occurrence match has three effects, always written
together so every surface agrees:

1. ``ExpectedMatch`` — what ``bills_remaining``, ``outstanding``,
   contributions and forecasts read as satisfied.
2. Schedule advancement — ``RecurringCommitment.next_date`` (the Bills
   overdue signal) advances past the settled occurrence; a
   subscription match records the observed price via
   ``record_price`` (renewal advance + price history + change events).
3. Re-materialisation — future expected rows re-price; the matched
   row survives ``_drop_future`` by FK.

``post_sync`` runs the household's deterministic settlement passes in
order — transfer pairing (legs must exist before they can match),
refund linking, transfer-rule matching, then scored reconciliation —
and is the single funnel every sync trigger drains into.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import contributions
from app.persistence.models import (
    Account,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    RecurringCommitment,
    Subscription,
    SubscriptionStatus,
    Transaction,
)

log = logging.getLogger(__name__)


def settle_occurrence(
    session: Session, movement: ExpectedMovement, txn: Transaction,
    *, manual: bool,
) -> ExpectedMatch | None:
    """Link ``txn`` to ``movement`` and advance its schedule.

    Idempotent — a leg already linked anywhere, or a movement with no
    outstanding amount, is a no-op. Returns the match row or None."""
    if txn.id in contributions._used_legs(session):
        return None
    outstanding = contributions.status(session, movement
                                       ).outstanding_pence
    if outstanding <= 0:
        return None
    match = contributions._link(
        session, movement, txn,
        min(abs(txn.amount_pence), outstanding), manual=manual)
    if movement.source_type == ExpectedSource.commitment:
        from app.domain import recurring, schedule
        c = session.get(RecurringCommitment, movement.source_id)
        # Only the current head advances — a match on an older or
        # later occurrence must not skip unpaid occurrences between.
        if (c is not None and c.active
                and c.next_date == movement.expected_date):
            c.last_generated = movement.expected_date
            c.generated_count = (c.generated_count or 0) + 1
            c.next_date = recurring.advance(
                movement.expected_date, c.frequency, c.day_of_month)
            schedule.rematerialize_commitment(session, c)
    elif movement.source_type == ExpectedSource.subscription:
        from app.domain import schedule, subscriptions
        sub = session.get(Subscription, movement.source_id)
        # cancelled subs keep the ExpectedMatch (the payment happened)
        # but reject record_price — no price history / renewal advance
        if sub is not None and sub.status != SubscriptionStatus.cancelled:
            subscriptions.record_price(
                session, sub, txn.date, abs(txn.amount_pence),
                transaction_id=txn.id)
            schedule.rematerialize_subscription(session, sub)
    return match


@dataclass(frozen=True)
class PostSyncStats:
    transfer_pairs: int
    refund_links: int
    transfer_matches: int
    scanned: int
    matched: int
    queued: int


def post_sync(session: Session, household_id: int, *,
              today: date | None = None) -> PostSyncStats:
    """Deterministic settlement pass after a bank ingest.

    Idempotent across repeat syncs, restarts and webhook replays —
    every writer it calls is guarded by an existing unique/used-leg/
    matched-key rule. Scored reconciliation never auto-accepts an
    ambiguous match; those queue for review exactly as before.
    """
    from app.domain import categorise
    from app.domain import reconcile as recon

    today = today or date.today()
    pairs = categorise.auto_pair_transfers(session, household_id)
    refunds = categorise.auto_link_refunds(session, household_id)
    transfer_matches = contributions.auto_match(
        session, household_id, today=today)
    scanned = matched = queued = 0
    for acct in session.scalars(
            select(Account).where(
                Account.household_id == household_id)).all():
        st = recon.reconcile_account(session, acct, today)
        scanned += st.scanned
        matched += st.matched
        queued += st.queued
    session.flush()
    return PostSyncStats(
        transfer_pairs=pairs, refund_links=refunds,
        transfer_matches=transfer_matches, scanned=scanned,
        matched=matched, queued=queued)
