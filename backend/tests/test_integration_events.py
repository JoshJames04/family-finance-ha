"""Issue #437 — durable integration event outbox + cursor feed.

ACs: events are written at committed transitions; dedup_key gives
once-per-entity semantics across replays; the feed drains by
monotonic cursor; payload detail is opt-in and read-time redacted.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1 import integration as integration_route
from app.domain import outbox, settle
from app.domain import sync as syncdom
from app.persistence.models import (
    Account,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    IntegrationEvent,
    RecurringCommitment,
    TransactionSource,
)

TODAY = date(2025, 1, 16)


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Bills",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _feed_types(session: Session, hid: int) -> list[str]:
    return [e.event_type for e in session.scalars(
        select(IntegrationEvent).where(
            IntegrationEvent.household_id == hid)
        .order_by(IntegrationEvent.id)).all()]


class TestEmitAndFeed:

    def test_cursor_orders_and_paginates(
            self, session: Session, household: Household):
        for i in range(5):
            outbox.emit(session, household.id, f"t.{i}")
        rows, cursor = outbox.feed(session, household.id,
                                   after=0, limit=2)
        assert [r.event_type for r in rows] == ["t.0", "t.1"]
        rows2, cursor2 = outbox.feed(session, household.id,
                                     after=cursor, limit=10)
        assert [r.event_type for r in rows2] == ["t.2", "t.3", "t.4"]
        rows3, cursor3 = outbox.feed(session, household.id,
                                     after=cursor2)
        assert rows3 == [] and cursor3 == cursor2

    def test_dedup_key_fires_once(
            self, session: Session, household: Household):
        first = outbox.emit(session, household.id, "x",
                            dedup_key="k1")
        second = outbox.emit(session, household.id, "x",
                             dedup_key="k1")
        assert first is not None and second is None
        assert len(_feed_types(session, household.id)) == 1

    def test_household_isolation(self, session: Session,
                                 household: Household):
        other = Household(name="Other")
        session.add(other)
        session.flush()
        outbox.emit(session, other.id, "theirs")
        outbox.emit(session, household.id, "ours")
        rows, _ = outbox.feed(session, household.id)
        assert [r.event_type for r in rows] == ["ours"]


class TestRedaction:

    def test_safe_strips_names_and_amounts(
            self, session: Session, household: Household):
        ev = outbox.emit(
            session, household.id, "movement.settled",
            payload={"label": "Netflix", "amount_pence": 1599,
                     "expected_date": "2025-01-15",
                     "payee": "NETFLIX.COM", "status": "settled"})
        safe = outbox.visible_payload(ev, detailed=False)
        assert "label" not in safe and "payee" not in safe
        assert "amount_pence" not in safe
        assert safe["expected_date"] == "2025-01-15"
        assert safe["status"] == "settled"
        full = outbox.visible_payload(ev, detailed=True)
        assert full["label"] == "Netflix"
        assert full["amount_pence"] == 1599

    def test_detail_toggle_persists(
            self, session: Session, household: Household):
        assert outbox.detail_enabled(session) is False
        outbox.set_detail(session, "detailed")
        assert outbox.detail_enabled(session) is True
        outbox.set_detail(session, "safe")
        assert outbox.detail_enabled(session) is False
        with pytest.raises(ValueError, match="safe"):
            outbox.set_detail(session, "everything")


class TestEmitterCoverage:

    def test_settlement_emits_movement_settled(
            self, session: Session, household: Household,
            account: Account):
        c = RecurringCommitment(
            household_id=household.id, name="Netflix",
            account_id=account.id, amount_pence=-1599,
            frequency=Frequency.monthly,
            next_date=date(2025, 1, 15))
        session.add(c)
        session.flush()
        mv = ExpectedMovement(
            household_id=household.id, account_id=account.id,
            expected_date=date(2025, 1, 15), amount_pence=-1599,
            label="Netflix", confidence=Confidence.scheduled,
            source_type=ExpectedSource.commitment, source_id=c.id)
        session.add(mv)
        session.flush()
        from app.persistence.models import Transaction, TransactionStatus
        t = Transaction(
            account_id=account.id, date=date(2025, 1, 15),
            amount_pence=-1599, payee="Netflix",
            status=TransactionStatus.cleared,
            source=TransactionSource.starling)
        session.add(t)
        session.flush()

        settle.post_sync(session, household.id, today=TODAY)

        types = _feed_types(session, household.id)
        assert "movement.settled" in types
        ev = session.scalars(select(IntegrationEvent).where(
            IntegrationEvent.event_type == "movement.settled")).one()
        assert ev.entity_kind == "commitment"
        assert ev.entity_id == c.id

    def test_overdue_emits_once_per_movement(
            self, session: Session, household: Household,
            account: Account):
        c = RecurringCommitment(
            household_id=household.id, name="Rent",
            account_id=account.id, amount_pence=-90000,
            frequency=Frequency.monthly,
            next_date=date(2025, 1, 10))
        session.add(c)
        session.flush()
        session.add(ExpectedMovement(
            household_id=household.id, account_id=account.id,
            expected_date=date(2025, 1, 10), amount_pence=-90000,
            label="Rent", confidence=Confidence.scheduled,
            source_type=ExpectedSource.commitment, source_id=c.id))
        session.flush()

        settle.post_sync(session, household.id, today=TODAY)
        settle.post_sync(session, household.id, today=TODAY)

        assert _feed_types(
            session, household.id).count("movement.overdue") == 1

    def test_ingest_emits_transaction_and_run_events(
            self, session: Session, household: Household,
            account: Account):
        item = syncdom.FeedItem(
            external_ref="ref-1", date=date(2025, 1, 14),
            amount_pence=-2500, payee="Grocer",
            status=None)
        page = syncdom.FeedPage(items=[item], cursor_ref=None)

        run = syncdom.sync_account(
            session, account, TransactionSource.starling,
            lambda _c: iter([page]))

        types = _feed_types(session, household.id)
        assert "transaction.imported" in types
        assert "sync.completed" in types
        assert run.inserted == 1
        txn_ev = session.scalars(select(IntegrationEvent).where(
            IntegrationEvent.event_type ==
            "transaction.imported")).one()
        assert txn_ev.payload["amount_pence"] == -2500

    def test_ingest_replay_dedupes(
            self, session: Session, household: Household,
            account: Account):
        item = syncdom.FeedItem(
            external_ref="ref-9", date=date(2025, 1, 14),
            amount_pence=-2500, payee="Grocer", status=None)
        syncdom.sync_account(
            session, account, TransactionSource.starling,
            lambda _c: iter([syncdom.FeedPage(
                items=[item], cursor_ref=None)]))
        # a resync of the same window must not re-emit
        assert _feed_types(session, household.id).count(
            "transaction.imported") == 1


class TestRoutes:

    def test_feed_route_honours_redaction(
            self, session: Session, household: Household):
        outbox.emit(session, household.id, "movement.settled",
                    payload={"label": "Netflix",
                             "expected_date": "2025-01-15"})
        resp = integration_route.events(
            household, session, after=0, limit=10)
        assert resp.events[0].data == {
            "expected_date": "2025-01-15"}
        outbox.set_detail(session, "detailed")
        resp = integration_route.events(
            household, session, after=0, limit=10)
        assert resp.events[0].data["label"] == "Netflix"

    def test_settings_roundtrip(
            self, session: Session, household: Household):
        out = integration_route.get_settings(household, session)
        assert out.detail == "safe"
        patched = integration_route.patch_settings(
            type("B", (), {"detail": "detailed"})(),
            household, session, "admin", None)
        assert patched.detail == "detailed"
        assert outbox.detail_enabled(session) is True
