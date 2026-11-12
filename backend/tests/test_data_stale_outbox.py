"""Canonical freshness transitions and integration resource visibility."""

from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.core.scheduler import sync_tick
from app.domain import outbox
from app.persistence.models import (
    Account,
    Budget,
    BudgetPeriodType,
    EnergyReading,
    IntegrationEvent,
    SyncCursor,
    TransactionSource,
)


def test_scheduled_tick_emits_stale_once_per_last_success(session, household):
    now = datetime(2025, 4, 10, 12)
    account = Account(
        household_id=household.id,
        name="Synthetic bank account",
        type="joint",
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    session.add(account)
    session.flush()
    cursor = SyncCursor(
        account_id=account.id,
        source=TransactionSource.starling,
        last_success_at=now - timedelta(hours=9),
    )
    session.add(cursor)
    session.flush()
    for _ in range(2):
        sync_tick(
            session,
            client=object(),
            token_for=lambda *_: "unused",
            interval_seconds=4 * 3600,
            now=now,
        )
    events = list(
        session.scalars(select(IntegrationEvent).where(IntegrationEvent.event_type == "data.stale"))
    )
    assert len(events) == 1
    assert outbox.visible_payload(events[0], False)["status"] == "stale"
    cursor.last_success_at = now
    session.flush()
    sync_tick(
        session,
        client=object(),
        token_for=lambda *_: "unused",
        interval_seconds=4 * 3600,
        now=now + timedelta(hours=9),
    )
    events = list(
        session.scalars(select(IntegrationEvent).where(IntegrationEvent.event_type == "data.stale"))
    )
    assert len(events) == 2


def test_canonical_shared_resource_events_are_visible(session, household):
    budget = Budget(
        household_id=household.id,
        name="Synthetic budget",
        period_type=BudgetPeriodType.monthly,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 30),
    )
    reading = EnergyReading(
        household_id=household.id, date=date(2025, 4, 10), wh=1000, source="synthetic"
    )
    session.add_all([budget, reading])
    session.flush()
    for kind, entity_id in [
        ("budget", budget.id),
        ("energy_reading", reading.id),
        ("backup", None),
    ]:
        event = outbox.emit(
            session,
            household.id,
            kind + ".test",
            entity_kind=kind,
            entity_id=entity_id,
            payload={"status": "completed"},
        )
        assert outbox.event_visible(session, event, None)
