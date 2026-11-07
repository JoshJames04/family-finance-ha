"""Issue #116 — notification rules engine.

ACs: same event re-evaluated never double-notifies; N rapid triggers
batch into a digest per config; snooze mutes until expiry.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import notifications as notify
from app.domain.notifications import NotificationError
from app.main import create_app
from app.persistence.models import (
    Account,
    Household,
    NotificationEvent,
    NotificationKind,
    NotificationState,
    Transaction,
    TransactionStatus,
)

NOW = datetime(2025, 4, 10, 12, 0, 0)


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Current",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _spend_rule(session: Session, household: Household,
                digest: bool = False, window: int = 60):
    return notify.create_rule(
        session, household.id, name="Big spend",
        kind=NotificationKind.spend_above,
        params={"threshold_pence": 10_000},
        digest=digest, digest_window_minutes=window)


def _spend(session: Session, a: Account, pence: int,
           on: date = date(2025, 4, 10)) -> Transaction:
    t = Transaction(account_id=a.id, date=on, amount_pence=-pence,
                    payee="shop", status=TransactionStatus.cleared,
                    source="manual")
    session.add(t)
    session.flush()
    return t


class TestIdempotency:
    def test_reevaluate_never_double_raises(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: same event re-evaluated never double-notifies."""
        rule = _spend_rule(session, household)
        _spend(session, account, 20_000)
        assert notify.evaluate(session, rule) == 1
        assert notify.evaluate(session, rule) == 0
        assert notify.evaluate(session, rule) == 0
        count = len([e for e in session.query(NotificationEvent)
                     if e.rule_id == rule.id])
        assert count == 1

    def test_second_dispatch_delivers_nothing(
            self, session: Session, household: Household,
            account: Account) -> None:
        rule = _spend_rule(session, household)
        _spend(session, account, 20_000)
        notify.evaluate(session, rule)
        r1 = notify.dispatch(session, household.id, now=NOW)
        r2 = notify.dispatch(session, household.id,
                             now=NOW + timedelta(hours=1))
        assert r1.events_delivered == 1
        assert r2.events_delivered == 0
        assert not r2.deliveries


class TestDigestBatching:
    def test_n_triggers_one_digest(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: N rapid triggers batch into a digest per config."""
        rule = _spend_rule(session, household, digest=True,
                           window=30)
        for _ in range(4):
            _spend(session, account, 20_000)
        assert notify.evaluate(session, rule, at=NOW) == 4
        # Inside the window — nothing flushes yet.
        r = notify.dispatch(session, household.id, now=NOW)
        assert r.events_delivered == 0
        # After the window — ONE delivery carrying all four.
        r = notify.dispatch(session, household.id,
                            now=NOW + timedelta(minutes=31))
        assert r.events_delivered == 4
        assert len(r.deliveries) == 1
        assert r.deliveries[0].event_count == 4

    def test_immediate_rules_deliver_each(
            self, session: Session, household: Household,
            account: Account) -> None:
        rule = _spend_rule(session, household, digest=False)
        for _ in range(3):
            _spend(session, account, 20_000)
        notify.evaluate(session, rule, at=NOW)
        r = notify.dispatch(session, household.id, now=NOW)
        assert r.events_delivered == 3
        assert len(r.deliveries) == 3


class TestBalanceBelow:
    def test_daily_dedup(self, session: Session,
                         household: Household,
                         account: Account) -> None:
        rule = notify.create_rule(
            session, household.id, name="Low",
            kind=NotificationKind.balance_below,
            params={"threshold_pence": 50_000,
                    "account_id": account.id})
        # Opening balance 0 < 50k → one event per day, not per eval.
        assert notify.evaluate(
            session, rule, today=date(2025, 4, 10)) == 1
        assert notify.evaluate(
            session, rule, today=date(2025, 4, 10)) == 0
        assert notify.evaluate(
            session, rule, today=date(2025, 4, 11)) == 1


class TestSnoozeAck:
    def test_snooze_skips_until_expiry(
            self, session: Session, household: Household,
            account: Account) -> None:
        rule = _spend_rule(session, household)
        _spend(session, account, 20_000)
        notify.evaluate(session, rule)
        e = session.query(NotificationEvent).one()
        notify.snooze(session, e, NOW + timedelta(hours=2))
        r = notify.dispatch(session, household.id,
                            now=NOW + timedelta(hours=1))
        assert r.events_delivered == 0
        r = notify.dispatch(session, household.id,
                            now=NOW + timedelta(hours=3))
        assert r.events_delivered == 1

    def test_snooze_delivered_rejected(
            self, session: Session, household: Household,
            account: Account) -> None:
        rule = _spend_rule(session, household)
        _spend(session, account, 20_000)
        notify.evaluate(session, rule)
        notify.dispatch(session, household.id, now=NOW)
        e = session.query(NotificationEvent).one()
        assert e.state == NotificationState.delivered
        with pytest.raises(NotificationError):
            notify.snooze(session, e, NOW + timedelta(hours=1))

    def test_ack_marks_event(
            self, session: Session, household: Household,
            account: Account) -> None:
        rule = _spend_rule(session, household)
        _spend(session, account, 20_000)
        notify.evaluate(session, rule)
        e = session.query(NotificationEvent).one()
        notify.ack(session, e, now=NOW)
        assert e.state == NotificationState.acked
        assert e.acked_at == NOW


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_rule_lifecycle(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", json={
            "name": "Current", "type": "personal",
            "opening_balance": 100_000,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        rid = client.post("/api/v1/notifications/rules", json={
            "name": "Low balance", "kind": "balance_below",
            "params": {"threshold_pence": 200_000,
                       "account_id": aid}},
            headers=h).json()["id"]
        r = client.post(f"/api/v1/notifications/rules/{rid}/evaluate",
                        headers=h)
        assert r.json()["raised"] == 1
        assert client.post(
            f"/api/v1/notifications/rules/{rid}/evaluate",
            headers=h).json()["raised"] == 0
        events = client.get("/api/v1/notifications/events",
                            headers=h).json()
        assert len(events) == 1
        d = client.post("/api/v1/notifications/dispatch",
                        headers=h).json()
        assert d["events_delivered"] == 1
        assert d["deliveries"][0]["channel"] == "ha_notify"
