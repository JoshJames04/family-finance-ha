"""Issue #119 — HA notify transport: privacy-safe compose, failure
capture, per-member service routing."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import deliver
from app.domain import notifications as notify
from app.main import create_app
from app.persistence.models import (
    Household,
    NotificationDelivery,
    NotificationKind,
)


class FakeNotifier:
    """Records calls; ``fail`` controls raising per service."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str | None, str, str]] = []
        self.fail = fail

    def notify(self, service: str | None, title: str,
               message: str) -> None:
        if self.fail:
            raise ConnectionError("ha unreachable")
        self.calls.append((service, title, message))


def _rule(session: Session, household: Household, **kw):
    return notify.create_rule(
        session, household.id, name=kw.pop("name", "Spend"),
        kind=kw.pop("kind", NotificationKind.spend_above),
        params={"threshold_pence": 100}, **kw)


def _deliverable(session: Session, rule, count: int = 1):
    for i in range(count):
        notify.raise_event(session, rule, f"k{i}", {
            "transaction_id": i, "payee": f"Shop{i}",
            "amount_pence": -4200, "date": "2025-03-01"})
    res = notify.dispatch(session, rule.household_id)
    assert res.deliveries, "expected a pending delivery"
    return res.deliveries


class TestCompose:
    def test_privacy_body_has_zero_digits(self, session, household):
        """AC: privacy-mode body contains zero digits/currency."""
        rule = _rule(session, household)
        events = [
            notify.raise_event(session, rule, f"k{i}", {
                "payee": f"Tesco{i}", "amount_pence": -4200})
            for i in range(3)]
        rule.digest = True
        title, message = deliver.compose(rule, [e for e in events
                                                if e is not None])
        assert re.search(r"\d", title + message) is None
        assert "£" not in message and "Tesco" not in message

    def test_detail_mode_shows_amounts(self, session, household):
        rule = _rule(session, household, privacy_safe=False)
        e = notify.raise_event(session, rule, "k1", {
            "payee": "Tesco", "amount_pence": -4200})
        title, message = deliver.compose(rule, [e])
        assert title == "Spend"
        assert "Tesco" in message and "£42.00" in message


class TestDelivery:
    def test_sent_marks_row(self, session, household):
        rule = _rule(session, household)
        _deliverable(session, rule)
        n = FakeNotifier()
        res = deliver.deliver_pending(session, household.id, n)
        assert res.sent == 1 and res.failed == 0
        d = session.scalar(select(NotificationDelivery))
        assert d.status == "sent" and d.sent_at is not None
        assert n.calls[0][0] is None     # no service → broadcast

    def test_per_member_service_routing(self, session, household):
        rule = _rule(session, household,
                     service="mobile_app_pixel")
        _deliverable(session, rule)
        n = FakeNotifier()
        deliver.deliver_pending(session, household.id, n)
        assert n.calls[0][0] == "mobile_app_pixel"

    def test_failure_captured_never_raised(self, session, household):
        """AC: delivery failure logged + surfaced, engine intact."""
        rule = _rule(session, household)
        _deliverable(session, rule)
        ok_rule = _rule(session, household, name="Bal",
                        kind=NotificationKind.balance_below,
                        service="svc_ok")
        _deliverable(session, ok_rule)

        class Partial(FakeNotifier):
            def notify(self, service, title, message):
                if service != "svc_ok":
                    raise ConnectionError("ha unreachable")
                super().notify(service, title, message)

        res = deliver.deliver_pending(
            session, household.id, Partial())
        assert res.sent == 1 and res.failed == 1
        rows = {d.rule_id: d for d in session.scalars(
            select(NotificationDelivery))}
        assert rows[rule.id].status == "failed"
        assert "unreachable" in rows[rule.id].error
        assert rows[ok_rule.id].status == "sent"


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_deliver_endpoint_surfaces_failures(
            self, client: TestClient):
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", json={
            "name": "A", "type": "personal",
            "opening_balance": 0, "opening_date": "2025-01-01"},
            headers=h).json()["id"]
        rid = client.post("/api/v1/notifications/rules", json={
            "name": "Big spend", "kind": "spend_above",
            "params": {"threshold_pence": 100}}, headers=h
        ).json()["id"]
        client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2025-03-01",
            "amount": -4200, "payee": "Shop"}, headers=h)
        client.post(f"/api/v1/notifications/rules/{rid}/evaluate",
                    headers=h)
        client.post("/api/v1/notifications/dispatch", headers=h)

        client.app.state.ha_notifier = FakeNotifier(fail=True)
        r = client.post("/api/v1/notifications/deliver", headers=h)
        assert r.json() == {"sent": 0, "failed": 1}
        rows = client.get("/api/v1/notifications/deliveries",
                          headers=h).json()
        assert rows[0]["status"] == "failed"
        assert "unreachable" in rows[0]["error"]

        client.app.state.ha_notifier = FakeNotifier()
        r = client.post("/api/v1/notifications/deliver", headers=h)
        assert r.json() == {"sent": 0, "failed": 0}  # failed stays

    def test_rule_fields_roundtrip(self, client: TestClient):
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        r = client.post("/api/v1/notifications/rules", json={
            "name": "N", "kind": "balance_below",
            "params": {"threshold_pence": 500},
            "service": "mobile_app_x", "privacy_safe": False},
            headers=h)
        assert r.status_code == 201
        assert r.json()["service"] == "mobile_app_x"
        assert r.json()["privacy_safe"] is False
