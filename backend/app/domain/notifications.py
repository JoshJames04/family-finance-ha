"""Issue #116 — notification rules engine.

Rules evaluate triggers into dedup-keyed ``NotificationEvent`` rows;
``dispatch`` batches pending events into ``NotificationDelivery``
rows — one per event for immediate rules, one digest row covering N
events for digest rules once the window has elapsed.

Idempotency is structural: ``(rule_id, dedup_key)`` is UNIQUE, and
``evaluate`` derives the key from the triggering entity
(``txn:{id}``, ``account:{id}:{date}``) — re-running the same scan
can never raise a second event or double-notify.

Delivery is channel-abstracted: the delivery row records the channel
(``ha_notify`` today) so a future channel needs no schema change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.accounts import current_balance
from app.persistence.models import (
    Account,
    NotificationDelivery,
    NotificationEvent,
    NotificationKind,
    NotificationRule,
    NotificationState,
    Transaction,
    TransactionStatus,
)


class NotificationError(ValueError):
    """Domain rejection → 422 at the API edge."""


@dataclass(frozen=True)
class DispatchResult:
    deliveries: list[NotificationDelivery]
    events_delivered: int


def create_rule(session: Session, household_id: int, *,
                name: str, kind: NotificationKind,
                params: dict[str, object] | None = None,
                target_profile_id: int | None = None,
                channel: str = "ha_notify",
                digest: bool = False,
                digest_window_minutes: int = 60) -> NotificationRule:
    r = NotificationRule(
        household_id=household_id, name=name, kind=kind,
        params=params or {}, target_profile_id=target_profile_id,
        channel=channel, digest=digest,
        digest_window_minutes=digest_window_minutes)
    session.add(r)
    session.flush()
    return r


def raise_event(session: Session, rule: NotificationRule,
                dedup_key: str, payload: dict[str, object],
                at: datetime | None = None
                ) -> NotificationEvent | None:
    """Insert an event iff (rule, dedup_key) is unseen. Returns the
    new event, or None when the trigger already fired — the AC
    'same event re-evaluated never double-notifies'. ``at`` injects
    the raise time for deterministic digest-window tests."""
    seen = session.scalar(
        select(NotificationEvent.id).where(
            NotificationEvent.rule_id == rule.id,
            NotificationEvent.dedup_key == dedup_key))
    if seen is not None:
        return None
    e = NotificationEvent(rule_id=rule.id, dedup_key=dedup_key,
                          payload=payload)
    if at is not None:
        e.created_at = at
    session.add(e)
    session.flush()
    return e


def _eval_spend_above(session: Session, rule: NotificationRule,
                      params: dict[str, object],
                      at: datetime | None = None) -> int:
    """One event per transaction strictly over ``threshold_pence``;
    dedup per transaction id."""
    threshold = int(str(params.get("threshold_pence", 0)))
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == rule.household_id,
               Transaction.amount_pence < -threshold,
               Transaction.status != TransactionStatus.void))
    if (aid := params.get("account_id")) is not None:
        stmt = stmt.where(Transaction.account_id == int(str(aid)))
    raised = 0
    for t in session.scalars(stmt):
        if raise_event(session, rule, f"txn:{t.id}", {
                "transaction_id": t.id, "payee": t.payee,
                "amount_pence": t.amount_pence,
                "date": t.date.isoformat()}, at=at) is not None:
            raised += 1
    return raised


def _eval_balance_below(session: Session, rule: NotificationRule,
                        params: dict[str, object], today: date,
                        at: datetime | None = None) -> int:
    """One event per account under ``threshold_pence``, deduped
    daily — a sustained dip notifies once, not every poll."""
    threshold = int(str(params.get("threshold_pence", 0)))
    stmt = select(Account).where(
        Account.household_id == rule.household_id)
    if (aid := params.get("account_id")) is not None:
        stmt = stmt.where(Account.id == int(str(aid)))
    raised = 0
    for a in session.scalars(stmt):
        bal = current_balance(session, a.id)
        if bal < threshold and raise_event(
                session, rule, f"account:{a.id}:{today.isoformat()}", {
                    "account_id": a.id, "balance_pence": bal,
                    "threshold_pence": threshold},
                at=at) is not None:
            raised += 1
    return raised


def evaluate(session: Session, rule: NotificationRule,
             today: date | None = None,
             at: datetime | None = None) -> int:
    """Run one rule's trigger scan; returns newly-raised count."""
    if not rule.enabled:
        return 0
    params = rule.params or {}
    if rule.kind is NotificationKind.spend_above:
        return _eval_spend_above(session, rule, params, at=at)
    if rule.kind is NotificationKind.balance_below:
        return _eval_balance_below(
            session, rule, params, today or date.today(), at=at)
    raise NotificationError(f"unknown rule kind {rule.kind}")


def dispatch(session: Session, household_id: int,
             now: datetime | None = None) -> DispatchResult:
    """Flush deliverable events into deliveries.

    Immediate rules: each pending event → its own delivery.
    Digest rules: pending events group per rule; the batch flushes
    as ONE delivery once the oldest event is older than the rule's
    digest window — N rapid triggers become one notification.
    Snoozed/acked events are skipped (snooze lifts at
    ``snoozed_until``).
    """
    now = now or datetime.now()
    rules = {r.id: r for r in session.scalars(
        select(NotificationRule).where(
            NotificationRule.household_id == household_id,
            NotificationRule.enabled.is_(True)))}
    if not rules:
        return DispatchResult([], 0)
    pending = session.scalars(
        select(NotificationEvent)
        .where(NotificationEvent.rule_id.in_(rules),
               NotificationEvent.state == NotificationState.pending,
               (NotificationEvent.snoozed_until.is_(None)
                | (NotificationEvent.snoozed_until <= now)))
        .order_by(NotificationEvent.id)).all()

    deliveries: list[NotificationDelivery] = []
    count = 0
    for rule_id in {e.rule_id for e in pending}:
        rule = rules[rule_id]
        events = [e for e in pending if e.rule_id == rule_id]
        if rule.digest:
            oldest = min(e.created_at for e in events)
            if oldest + timedelta(
                    minutes=rule.digest_window_minutes) > now:
                continue                # window still open — wait
            batches = [events]          # one digest covering N events
        else:
            batches = [[e] for e in events]  # immediate: one each
        for batch in batches:
            delivery = NotificationDelivery(
                household_id=household_id, rule_id=rule_id,
                channel=rule.channel, event_count=len(batch))
            session.add(delivery)
            session.flush()
            for e in batch:
                e.state = NotificationState.delivered
                e.delivery_id = delivery.id
                e.delivered_at = now
            deliveries.append(delivery)
            count += len(batch)
    return DispatchResult(deliveries, count)


def ack(session: Session, event: NotificationEvent,
        now: datetime | None = None) -> None:
    event.state = NotificationState.acked
    event.acked_at = now or datetime.now()


def snooze(session: Session, event: NotificationEvent,
           until: datetime) -> None:
    """Mute a pending event until ``until`` — it stays ``pending``
    with ``snoozed_until`` set, so lifting the snooze is automatic
    (delivered events can't be snoozed; they get ``ack``)."""
    if event.state != NotificationState.pending:
        raise NotificationError("only pending events can be snoozed")
    event.snoozed_until = until
