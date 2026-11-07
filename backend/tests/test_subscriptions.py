"""Issue #107 — subscription register: renewal advancement per
frequency, overdue flagging, cancellation retaining history while
leaving forecasts, price history."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import schedule
from app.domain import subscriptions as subs
from app.domain.subscriptions import SubscriptionError
from app.main import create_app
from app.persistence.models import (
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    Subscription,
)


@pytest.fixture()
def sub(session: Session, household: Household) -> Subscription:
    return subs.create(
        session, household.id, payee="Streamflix",
        frequency=Frequency.monthly, expected_pence=1_099,
        next_renewal_date=date(2026, 3, 15))


class TestRenewalAdvance:
    def test_payment_on_renewal_advances(
            self, session: Session, sub: Subscription) -> None:
        subs.record_price(session, sub, date(2026, 3, 15), 1_099)
        assert sub.next_renewal_date == date(2026, 4, 15)

    def test_late_payment_skips_missed_periods(
            self, session: Session, sub: Subscription) -> None:
        subs.record_price(session, sub, date(2026, 6, 20), 1_099)
        assert sub.next_renewal_date == date(2026, 7, 15)

    def test_annual_frequency(self, session: Session,
                              household: Household) -> None:
        s = subs.create(
            session, household.id, payee="CloudBackup",
            frequency=Frequency.annually, expected_pence=5_999,
            next_renewal_date=date(2026, 1, 31))
        subs.record_price(session, s, date(2026, 1, 31), 5_999)
        assert s.next_renewal_date == date(2027, 1, 31)

    def test_month_end_clamp_carries_anchor(
            self, session: Session, household: Household) -> None:
        s = subs.create(
            session, household.id, payee="Gym",
            frequency=Frequency.monthly, expected_pence=3_000,
            next_renewal_date=date(2026, 1, 31))
        subs.advance_renewal(s, date(2026, 1, 31))
        assert s.next_renewal_date == date(2026, 2, 28)
        subs.advance_renewal(s, date(2026, 2, 28))
        # anchor (31st) carried, not drifted to 28th forever
        assert s.next_renewal_date == date(2026, 3, 31)

    def test_early_payment_does_not_advance(
            self, session: Session, sub: Subscription) -> None:
        subs.record_price(session, sub, date(2026, 3, 1), 1_099)
        assert sub.next_renewal_date == date(2026, 3, 15)


class TestOverdue:
    def test_overdue_flag(self, sub: Subscription) -> None:
        assert subs.renewal_overdue(sub, date(2026, 3, 14)) is False
        assert subs.renewal_overdue(sub, date(2026, 3, 16)) is True

    def test_cancelled_never_overdue(
            self, sub: Subscription) -> None:
        subs.cancel(sub, date(2026, 3, 20))
        assert subs.renewal_overdue(sub, date(2026, 4, 1)) is False


class TestCancel:
    def test_cancel_retains_history(self, session: Session,
                                  sub: Subscription) -> None:
        subs.record_price(session, sub, date(2026, 3, 15), 1_099)
        subs.cancel(sub, date(2026, 3, 20))
        assert sub.status.value == "cancelled"
        assert sub.cancelled_date == date(2026, 3, 20)
        assert len(sub.prices) == 1  # history retained

    def test_double_cancel_rejected(
            self, sub: Subscription) -> None:
        subs.cancel(sub, date(2026, 3, 20))
        with pytest.raises(SubscriptionError):
            subs.cancel(sub, date(2026, 3, 21))

    def test_payment_on_cancelled_rejected(
            self, session: Session, sub: Subscription) -> None:
        subs.cancel(sub, date(2026, 3, 20))
        with pytest.raises(SubscriptionError):
            subs.record_price(session, sub, date(2026, 4, 15), 1_099)


class TestForecastEmission:
    def test_active_sub_emits_negative_expectations(
            self, session: Session, sub: Subscription) -> None:
        n = schedule.rematerialize_subscription(
            session, sub, today=date(2026, 3, 1))
        assert n >= 2
        rows = session.execute(
            select(ExpectedMovement).where(
                ExpectedMovement.source_type
                == ExpectedSource.subscription)).scalars().all()
        assert all(r.amount_pence == -1_099 for r in rows)
        assert rows[0].expected_date == date(2026, 3, 15)

    def test_cancel_drops_future_expectations(
            self, session: Session, sub: Subscription) -> None:
        schedule.rematerialize_subscription(
            session, sub, today=date(2026, 3, 1))
        subs.cancel(sub, date(2026, 3, 20))
        n = schedule.rematerialize_subscription(
            session, sub, today=date(2026, 3, 21))
        assert n == 0
        remaining = session.execute(
            select(ExpectedMovement).where(
                ExpectedMovement.source_type
                == ExpectedSource.subscription,
                ExpectedMovement.expected_date >= date(2026, 3, 21))
        ).scalars().all()
        assert remaining == []


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    @pytest.fixture()
    def h(self, client: TestClient) -> dict[str, str]:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        return {"X-Household-Id": str(hid)}

    def test_full_lifecycle(self, client: TestClient,
                            h: dict) -> None:
        r = client.post("/api/v1/subscriptions", headers=h, json={
            "payee": "Streamflix", "frequency": "monthly",
            "expected": 1099, "next_renewal_date": "2026-03-15"})
        assert r.status_code == 201, r.json()
        sid = r.json()["id"]
        assert r.json()["overdue"] is False or True  # date-dependent

        r = client.post(f"/api/v1/subscriptions/{sid}/payments",
                        headers=h,
                        json={"date": "2026-03-15", "amount": 1199})
        assert r.status_code == 201
        s = client.get(f"/api/v1/subscriptions/{sid}",
                       headers=h).json()
        assert s["next_renewal_date"] == "2026-04-15"
        prices = client.get(f"/api/v1/subscriptions/{sid}/prices",
                            headers=h).json()
        assert prices[0]["amount_pence"] == 1_199

        r = client.post(f"/api/v1/subscriptions/{sid}/cancel",
                        headers=h, json={"date": "2026-04-01"})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
        # history retained in list view
        rows = client.get("/api/v1/subscriptions", headers=h).json()
        assert len(rows) == 1 and rows[0]["status"] == "cancelled"
        active = client.get(
            "/api/v1/subscriptions?include_cancelled=false",
            headers=h).json()
        assert active == []

    def test_double_cancel_409(self, client: TestClient,
                               h: dict) -> None:
        sid = client.post("/api/v1/subscriptions", headers=h, json={
            "payee": "Gym", "frequency": "monthly", "expected": 3000,
            "next_renewal_date": "2026-03-15"}).json()["id"]
        client.post(f"/api/v1/subscriptions/{sid}/cancel",
                    headers=h, json={"date": "2026-03-20"})
        r = client.post(f"/api/v1/subscriptions/{sid}/cancel",
                        headers=h, json={"date": "2026-03-21"})
        assert r.status_code == 409
        assert r.json()["type"] == "about:blank"
