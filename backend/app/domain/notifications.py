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

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.orm import Session

from app.domain.accounts import current_balance
from app.persistence.models import (
    Account,
    AnomalyFlag,
    AnomalyStatus,
    Employment,
    PayrollRun,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
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
                digest_window_minutes: int = 60,
                service: str | None = None,
                privacy_safe: bool = True) -> NotificationRule:
    r = NotificationRule(
        household_id=household_id, name=name, kind=kind,
        params=params or {}, target_profile_id=target_profile_id,
        channel=channel, digest=digest,
        digest_window_minutes=digest_window_minutes,
        service=service, privacy_safe=privacy_safe)
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


def _owner_scope(rule: NotificationRule) -> ColumnElement[bool]:
    return or_(Account.owner_profile_id.is_(None),
               Account.owner_profile_id == rule.target_profile_id)


def _account_ids(rule: NotificationRule) -> Select[int]:
    return select(Account.id).where(Account.household_id == rule.household_id,
                                    _owner_scope(rule))


def _movement_scope(rule: NotificationRule) -> ColumnElement[bool]:
    employments = select(Employment.id).where(
        Employment.household_id == rule.household_id,
        Employment.profile_id == rule.target_profile_id)
    payrolls = select(PayrollRun.id).where(PayrollRun.employment_id.in_(employments))
    salary_owner = or_(
        ExpectedMovement.source_type.not_in([ExpectedSource.employment, ExpectedSource.payroll_run]),
        and_(ExpectedMovement.source_type == ExpectedSource.employment,
             ExpectedMovement.source_id.in_(employments)),
        and_(ExpectedMovement.source_type == ExpectedSource.payroll_run,
             ExpectedMovement.source_id.in_(payrolls)))
    return and_(or_(ExpectedMovement.account_id.in_(_account_ids(rule)),
                    ExpectedMovement.account_id.is_(None)), salary_owner)


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
               _owner_scope(rule),
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
        Account.household_id == rule.household_id, _owner_scope(rule))
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
    if rule.kind is NotificationKind.weekly_digest:
        return _eval_weekly_digest(
            session, rule, params, today or date.today(), at=at)
    if rule.kind is NotificationKind.payday_reminder:
        return _eval_payday_reminder(
            session, rule, params, today or date.today(), at=at)
    if rule.kind is NotificationKind.bill_reminder:
        return _eval_bill_reminder(
            session, rule, params, today or date.today(), at=at)
    # issue #118 — condition alerts
    if rule.kind is NotificationKind.low_projected_balance:
        return _eval_low_projected(
            session, rule, params, today or date.today(), at=at)
    if rule.kind is NotificationKind.budget_overspend:
        return _eval_budget_overspend(
            session, rule, params, today or date.today(), at=at)
    if rule.kind is NotificationKind.unusual_transaction:
        return _eval_unusual(session, rule, params, at=at)
    if rule.kind is NotificationKind.price_change:
        return _eval_price_change(session, rule, params, at=at)
    if rule.kind is NotificationKind.missed_transfer:
        return _eval_missed_transfer(
            session, rule, params, today or date.today(), at=at)
    if rule.kind is NotificationKind.sync_failure:
        return _eval_sync_failure(session, rule, params, at=at)
    if rule.kind is NotificationKind.payroll_variance:
        return 0
    if rule.kind is NotificationKind.payslip_review:
        return 0
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


def _eval_weekly_digest(session: Session, rule: NotificationRule,
                        params: dict[str, object], today: date,
                        at: datetime | None = None) -> int:
    """Issue #117 � one digest event per ISO week, raised on the
    configured weekday. Content: last-7-day spend, the next 7 days'
    liabilities, and notable items (pending anomaly count)."""
    weekday = int(str(params.get("weekday", 0)))
    if today.weekday() != weekday:
        return 0
    week_start = today - timedelta(days=6)
    spent = session.scalar(
        select(func.coalesce(func.sum(-Transaction.amount_pence), 0))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == rule.household_id,
               _owner_scope(rule),
               Transaction.date >= week_start,
               Transaction.date <= today,
               Transaction.amount_pence < 0,
               Transaction.status != TransactionStatus.void)) or 0
    upcoming = session.scalars(
        select(ExpectedMovement).where(
            ExpectedMovement.household_id == rule.household_id,
            _movement_scope(rule),
            ExpectedMovement.expected_date >= today,
            ExpectedMovement.expected_date <= today + timedelta(days=6),
            ExpectedMovement.amount_pence < 0,
            ExpectedMovement.dismissed_at.is_(None),
            # settled movements are no longer a liability (issue #429)
            ~select(ExpectedMatch.id).where(
                ExpectedMatch.movement_id ==
                ExpectedMovement.id).exists())
        .order_by(ExpectedMovement.amount_pence)).all()
    anomalies = session.scalar(
        select(func.count()).select_from(AnomalyFlag).where(
            AnomalyFlag.household_id == rule.household_id,
            AnomalyFlag.account_id.in_(_account_ids(rule)),
            AnomalyFlag.status == AnomalyStatus.pending)) or 0
    iso = today.isocalendar()
    liabilities = sum(-m.amount_pence for m in upcoming)
    lines = [
        f"Spent last 7 days: {spent}p",
        f"Coming week: {len(upcoming)} payments totalling {liabilities}p",
    ]
    for m in upcoming[:5]:
        lines.append(f"  {m.expected_date.isoformat()} {m.label} "
                     f"{m.amount_pence}p")
    if anomalies:
        lines.append(f"{anomalies} unusual transaction(s) to review")
    return 1 if raise_event(
        session, rule,
        f"digest:{iso.year}-W{iso.week:02d}",
        {"title": "Weekly finance summary",
         "lines": lines,
         "spent_pence": int(spent),
         "liabilities_pence": int(liabilities),
         "pending_anomalies": int(anomalies)},
        at=at) is not None else 0


def _eval_payday_reminder(session: Session, rule: NotificationRule,
                          params: dict[str, object], today: date,
                          at: datetime | None = None) -> int:
    """Issue #117 � one event per pay date, ``days_before`` ahead.
    Dedup per date: re-evaluating the same day can't double-remind."""
    days_before = int(str(params.get("days_before", 1)))
    target = today + timedelta(days=days_before)
    paydays = session.scalars(
        select(ExpectedMovement).where(
            ExpectedMovement.household_id == rule.household_id,
            _movement_scope(rule),
            ExpectedMovement.source_type.in_(
                [ExpectedSource.payroll_run, ExpectedSource.employment]),
            ExpectedMovement.amount_pence > 0,
            ExpectedMovement.expected_date == target,
            ExpectedMovement.dismissed_at.is_(None))).all()
    if not paydays:
        return 0
    total = sum(m.amount_pence for m in paydays)
    return 1 if raise_event(
        session, rule, f"payday:{target.isoformat()}",
        {"title": f"Payday on {target.isoformat()}",
         "date": target.isoformat(),
         "total_pence": total,
         "count": len(paydays)},
        at=at) is not None else 0


def _eval_bill_reminder(session: Session, rule: NotificationRule,
                        params: dict[str, object], today: date,
                        at: datetime | None = None) -> int:
    """Issue #117 � one event per upcoming out-movement inside the
    ``days_before`` window (inclusive). Dedup per movement+date so a
    bill inside the window on consecutive polls notifies once."""
    days_before = int(str(params.get("days_before", 3)))
    end = today + timedelta(days=days_before)
    bills = session.scalars(
        select(ExpectedMovement).where(
            ExpectedMovement.household_id == rule.household_id,
            _movement_scope(rule),
            ExpectedMovement.amount_pence < 0,
            ExpectedMovement.expected_date >= today,
            ExpectedMovement.expected_date <= end,
            ExpectedMovement.dismissed_at.is_(None),
            # already paid — do not remind (issue #429)
            ~select(ExpectedMatch.id).where(
                ExpectedMatch.movement_id ==
                ExpectedMovement.id).exists())
        .order_by(ExpectedMovement.expected_date)).all()
    raised = 0
    for m in bills:
        if raise_event(
                session, rule,
                f"bill:{m.id}:{m.expected_date.isoformat()}",
                {"title": f"{m.label} due {m.expected_date.isoformat()}",
                 "movement_id": m.id,
                 "label": m.label,
                 "amount_pence": m.amount_pence,
                 "expected_date": m.expected_date.isoformat()},
                at=at) is not None:
            raised += 1
    return raised


# ---- issue #118: condition alerts ----------------------------------------

SEVERITY = {
    NotificationKind.low_projected_balance: "warning",
    NotificationKind.budget_overspend: "warning",
    NotificationKind.unusual_transaction: "info",
    NotificationKind.price_change: "warning",
    NotificationKind.missed_transfer: "critical",
    NotificationKind.sync_failure: "critical",
}


def _clears(session: Session, rule: NotificationRule, kind: str,
            active: set[str], params: dict[str, object],
            at: datetime | None = None) -> int:
    """Optional all-clear (params: ``{"all_clear": true}``): entities
    that previously raised a ``kind:…`` event and are no longer active
    get exactly one ``…:clear`` event."""
    if not params.get("all_clear"):
        return 0
    raised = 0
    prefix = f"{kind}:"
    prior = session.scalars(
        select(NotificationEvent).where(
            NotificationEvent.rule_id == rule.id,
            NotificationEvent.dedup_key.like(f"{prefix}%"))).all()
    fired = {e.dedup_key for e in prior}
    for key in fired - active:
        if key.endswith(":clear") or f"{key}:clear" in fired:
            continue
        if raise_event(session, rule, f"{key}:clear",
                       {"title": "All clear", "cleared": key,
                        "severity": "info"}, at=at) is not None:
            raised += 1
    return raised


def _eval_low_projected(session: Session, rule: NotificationRule,
                        params: dict[str, object], today: date,
                        at: datetime | None = None) -> int:
    """Per-account projected minimum under ``threshold_pence`` within
    ``days`` (default 30). Daily dedup; severity warning."""
    from app.domain.projection import project

    threshold = int(str(params.get("threshold_pence", 0)))
    days = int(str(params.get("days", 30)))
    stmt = select(Account).where(
        Account.household_id == rule.household_id,
               _owner_scope(rule),
        Account.archived.is_(False))
    if (aid := params.get("account_id")) is not None:
        stmt = stmt.where(Account.id == int(str(aid)))
    raised = 0
    active_keys: set[str] = set()
    for a in session.scalars(stmt):
        proj = project(session, a, days, from_date=today)
        if proj.min_balance_pence < threshold:
            key = f"lowbal:{a.id}"
            active_keys.add(key)
            if raise_event(session, rule, key, {
                    "title": f"{a.name} projected low",
                    "account_id": a.id,
                    "min_balance_pence": proj.min_balance_pence,
                    "min_balance_date": proj.min_balance_date.isoformat()
                    if proj.min_balance_date else None,
                    "threshold_pence": threshold,
                    "severity": SEVERITY[
                        NotificationKind.low_projected_balance]},
                    at=at) is not None:
                raised += 1
    return raised + _clears(session, rule, "lowbal",
                            active_keys, params, at=at)


def _eval_budget_overspend(session: Session, rule: NotificationRule,
                           params: dict[str, object], today: date,
                           at: datetime | None = None) -> int:
    """Expense budget lines whose actual spend exceeds the effective
    amount over the budget period — one event per line per period."""
    from app.persistence.models import Budget, BudgetLine, BudgetLineSide

    lines = session.execute(
        select(BudgetLine, Budget).join(
            Budget, BudgetLine.budget_id == Budget.id)
        .where(Budget.household_id == rule.household_id,
               BudgetLine.side == BudgetLineSide.expense)).all()
    raised = 0
    active_keys: set[str] = set()
    for line, budget in lines:
        from app.domain import budgets
        # Read the same owner/split/transfer-aware actuals as budget reports.
        if (budget.owner_profile_id is not None
                and budget.owner_profile_id != rule.target_profile_id):
            continue
        effective = budgets.effective_pence(line)
        spent = budgets.actuals_for_budget(session, budget).get(line.id, 0)
        if spent > effective:
            key = f"overspend:{line.id}:{budget.start_date.isoformat()}"
            active_keys.add(key)
            if raise_event(session, rule, key, {
                    "title": "Budget line overspent",
                    "budget_id": budget.id,
                    "budget_line_id": line.id,
                    "category_id": line.category_id,
                    "effective_pence": effective,
                    "spent_pence": int(spent),
                    "severity": SEVERITY[
                        NotificationKind.budget_overspend]},
                    at=at) is not None:
                raised += 1
    return raised + _clears(session, rule, "overspend",
                            active_keys, params, at=at)


def _eval_unusual(session: Session, rule: NotificationRule,
                  params: dict[str, object],
                  at: datetime | None = None) -> int:
    """Pending anomaly flags → one event per flag (severity info —
    unusual ≠ bad)."""
    flags = session.scalars(
        select(AnomalyFlag).where(
            AnomalyFlag.household_id == rule.household_id,
            AnomalyFlag.account_id.in_(_account_ids(rule)),
            AnomalyFlag.status == AnomalyStatus.pending)).all()
    raised = 0
    for f in flags:
        if raise_event(session, rule, f"anomaly:{f.id}", {
                "title": "Unusual transaction to review",
                "anomaly_id": f.id,
                "transaction_id": f.transaction_id,
                "trigger": f.trigger.value,
                "severity": SEVERITY[
                    NotificationKind.unusual_transaction]},
                at=at) is not None:
            raised += 1
    return raised


def _eval_price_change(session: Session, rule: NotificationRule,
                       params: dict[str, object],
                       at: datetime | None = None) -> int:
    """Subscription PriceChangeEvents → one alert each (severity
    warning — the change may need a budget revision)."""
    from app.persistence.models import PriceChangeEvent, Subscription

    events = session.scalars(
        select(PriceChangeEvent).where(
            PriceChangeEvent.household_id == rule.household_id,
            PriceChangeEvent.subscription_id.in_(select(Subscription.id).where(or_(
                Subscription.account_id.is_(None),
                Subscription.account_id.in_(_account_ids(rule))))))).all()
    raised = 0
    for e in events:
        if raise_event(session, rule, f"price:{e.id}", {
                "title": "Subscription price changed",
                "price_change_id": e.id,
                "subscription_id": e.subscription_id,
                "old_pence": e.old_pence,
                "new_pence": e.new_pence,
                "delta_pence": e.delta_pence,
                "detected_date": e.detected_date.isoformat(),
                "severity": SEVERITY[NotificationKind.price_change]},
                at=at) is not None:
            raised += 1
    return raised


def _eval_missed_transfer(session: Session, rule: NotificationRule,
                          params: dict[str, object], today: date,
                          at: datetime | None = None) -> int:
    """Transfer-rule expected movements whose date has passed without
    being fulfilled or dismissed — severity critical (money that was
    meant to move didn't). ``grace_days`` waits before alerting."""
    grace = int(str(params.get("grace_days", 1)))
    overdue = today - timedelta(days=grace)
    missed = session.scalars(
        select(ExpectedMovement).where(
            ExpectedMovement.household_id == rule.household_id,
            _movement_scope(rule),
            ExpectedMovement.source_type == ExpectedSource.transfer_rule,
            ExpectedMovement.expected_date <= overdue,
            ExpectedMovement.dismissed_at.is_(None),
            # already fulfilled — not missed (issue #429)
            ~select(ExpectedMatch.id).where(
                ExpectedMatch.movement_id ==
                ExpectedMovement.id).exists())).all()
    raised = 0
    for m in missed:
        if raise_event(session, rule, f"missed:{m.id}", {
                "title": f"Missed transfer: {m.label}",
                "movement_id": m.id,
                "label": m.label,
                "amount_pence": m.amount_pence,
                "expected_date": m.expected_date.isoformat(),
                "severity": SEVERITY[NotificationKind.missed_transfer]},
                at=at) is not None:
            raised += 1
    return raised


def _eval_sync_failure(session: Session, rule: NotificationRule,
                       params: dict[str, object],
                       at: datetime | None = None) -> int:
    """Sync cursors in ``failed`` state → one alert per cursor per
    failure episode; all-clear when the cursor recovers."""
    from app.persistence.models import SyncCursor, SyncStatus

    cursors = session.scalars(
        select(SyncCursor)
        .join(Account, SyncCursor.account_id == Account.id)
        .where(Account.household_id == rule.household_id, _owner_scope(rule))).all()
    raised = 0
    active_keys: set[str] = set()
    for c in cursors:
        if c.status is SyncStatus.failed:
            key = f"syncfail:{c.id}"
            active_keys.add(key)
            if raise_event(session, rule, key, {
                    "title": "Bank sync failed",
                    "cursor_id": c.id,
                    "account_id": c.account_id,
                    "source": c.source.value,
                    "retry_at": c.retry_at.isoformat()
                    if c.retry_at else None,
                    "severity": SEVERITY[NotificationKind.sync_failure]},
                    at=at) is not None:
                raised += 1
    return raised + _clears(session, rule, "syncfail",
                            active_keys, params, at=at)

