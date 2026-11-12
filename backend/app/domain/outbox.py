"""Integration event outbox (issue #437 / ADR-0009).

The authoritative feed the Home Assistant integration drains:
writers call ``emit`` inside the same DB transaction as the state
change, so a committed event is never lost and a rolled-back change
never ghosts one. ``dedup_key`` gives once-per-entity transitions
idempotency across syncs, replays and restarts.

Payloads are written in full; ``feed`` redacts at read time by the
persisted ``ha_event_detail`` app setting — ``safe`` (default) keeps
structure/status/dates only; ``detailed`` (admin opt-in) adds names,
payees and pence amounts.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import AppMeta, IntegrationEvent

DETAIL_KEY = "ha_event_detail"

# Fields safe to expose on the HA event bus without opt-in —
# structure, status and dates; never names, payees or amounts.
_SAFE_KEYS = frozenset({
    "source", "source_type", "status", "old_status", "new_status",
    "state", "date", "expected_date", "occurrence_date", "direction",
    "count", "inserted", "deduped", "skipped", "currency", "manual",
    "frequency", "lead_days",
})


def detail_enabled(session: Session) -> bool:
    row = session.get(AppMeta, DETAIL_KEY)
    return row is not None and row.value == "detailed"


def set_detail(session: Session, mode: str) -> None:
    if mode not in ("safe", "detailed"):
        raise ValueError("ha_event_detail must be 'safe' or 'detailed'")
    row = session.get(AppMeta, DETAIL_KEY)
    if row is None:
        row = AppMeta(key=DETAIL_KEY, value=mode)
        session.add(row)
    else:
        row.value = mode
    session.flush()


def emit(session: Session, household_id: int, event_type: str, *,
         entity_kind: str | None = None, entity_id: int | None = None,
         payload: dict[str, object] | None = None, dedup_key: str | None = None,
         at: datetime | None = None) -> IntegrationEvent | None:
    """Append an outbox row. A ``dedup_key`` already seen returns
    None — the transition fires once, period."""
    if dedup_key is not None:
        seen = session.execute(
            select(IntegrationEvent.id).where(
                IntegrationEvent.dedup_key == dedup_key)
        ).scalar_one_or_none()
        if seen is not None:
            return None
    instant = at or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    row = IntegrationEvent(
        household_id=household_id, event_type=event_type,
        entity_kind=entity_kind, entity_id=entity_id,
        occurred_at=instant.astimezone(UTC).replace(tzinfo=None), payload=payload or {},
        dedup_key=dedup_key)
    session.add(row)
    session.flush()
    return row


def feed(session: Session, household_id: int, *,
         after: int = 0, limit: int = 100,
         ) -> tuple[list[IntegrationEvent], int]:
    """Ordered drain from cursor ``after``; returns (rows, cursor)."""
    rows = list(session.scalars(
        select(IntegrationEvent).where(
            IntegrationEvent.household_id == household_id,
            IntegrationEvent.id > after)
        .order_by(IntegrationEvent.id).limit(limit)).all())
    cursor = rows[-1].id if rows else after
    return rows, cursor


def visible_payload(event: IntegrationEvent, detailed: bool) -> dict[str, object]:
    """Read-time redaction — safe mode strips anything that could
    name or value a real record (ADR-0009)."""
    if detailed and event.entity_kind != "backup":
        return dict(event.payload)
    return {k: v for k, v in event.payload.items() if k in _SAFE_KEYS}


def event_visible(session: Session, event: IntegrationEvent, profile_id: int | None) -> bool:
    """Apply account ownership even in safe mode: ids and dates are private too."""
    from app.persistence import models
    entities = {
        "account": models.Account, "transaction": models.Transaction,
        "commitment": models.RecurringCommitment, "subscription": models.Subscription,
        "employment": models.Employment, "payroll_run": models.PayrollRun,
        "loan": models.Loan, "savings_goal": models.SavingsGoal,
        "expected_movement": models.ExpectedMovement,
        "budget": models.Budget, "budget_line": models.BudgetLine,
        "budgetline": models.BudgetLine, "energy_reading": models.EnergyReading,
        "gift": models.Gift, "payslip": models.Payslip,
        "seasonal_event": models.SeasonalEvent, "energy_config": models.EnergyConfig,
    }
    if event.entity_kind == "backup":
        return True
    if event.entity_kind is None and event.entity_id is None:
        return True
    if event.entity_kind in {"manual", "transfer_rule"}:
        account_id = event.payload.get("account_id")
        account = session.get(models.Account, account_id) if isinstance(account_id, int) else None
        return (account is not None and account.household_id == event.household_id
                and (account.owner_profile_id is None or account.owner_profile_id == profile_id))
    model = entities.get(event.entity_kind or "")
    if model is None or event.entity_id is None:
        return False
    entity = session.get(model, event.entity_id)
    if entity is None:
        return False
    if isinstance(entity, models.BudgetLine):
        entity = session.get(models.Budget, entity.budget_id)
    elif isinstance(entity, models.Gift):
        entity = session.get(models.SeasonalEvent, entity.event_id)
    elif isinstance(entity, models.Payslip):
        entity = session.get(models.PayrollRun, entity.payroll_run_id)
    if entity is None:
        return False
    if isinstance(entity, models.PayrollRun):
        entity = session.get(models.Employment, entity.employment_id)
        if entity is None:
            return False
    if getattr(entity, "household_id", event.household_id) != event.household_id:
        return False
    owner = getattr(entity, "owner_profile_id", getattr(entity, "profile_id", None))
    if owner is not None and owner != profile_id:
        return False
    account_id = getattr(entity, "account_id", None)
    if account_id is not None:
        entity = session.get(models.Account, account_id)
        if entity is None:
            return False
    owner = getattr(entity, "owner_profile_id", None)
    return owner is None or owner == profile_id



def latest_visible_cursor(session: Session, household_id: int, profile_id: int | None) -> int:
    rows = session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.household_id == household_id).order_by(
            IntegrationEvent.id.desc()).execution_options(yield_per=100))
    return next((row.id for row in rows if event_visible(session, row, profile_id)), 0)
