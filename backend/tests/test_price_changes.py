"""Issue #108 — price-change detection: a matching charge outside
tolerance records exactly one event (old/new/date); repeat charges at
the new price do not re-fire; variable DDs are excluded by
classification (they can never become subscriptions)."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import detection
from app.domain import subscriptions as subs
from app.main import create_app
from app.persistence.models import (
    Account,
    Frequency,
    Household,
    PriceChangeEvent,
    Subscription,
    Transaction,
    TransactionStatus,
)


@pytest.fixture()
def sub(session: Session, household: Household) -> Subscription:
    return subs.create(
        session, household.id, payee="Streamflix",
        frequency=Frequency.monthly, expected_pence=1_099,
        next_renewal_date=date(2026, 3, 15))


def _events(session: Session, sid: int) -> list[PriceChangeEvent]:
    return list(session.execute(
        select(PriceChangeEvent).where(
            PriceChangeEvent.subscription_id == sid)
        .order_by(PriceChangeEvent.detected_date)
    ).scalars().all())


class TestDetection:
    def test_planted_rise_records_exactly_one_event(
            self, session: Session, sub: Subscription) -> None:
        subs.record_price(session, sub, date(2026, 3, 15), 1_099)
        subs.record_price(session, sub, date(2026, 4, 15), 1_299)
        evs = _events(session, sub.id)
        assert len(evs) == 1
        assert evs[0].detected_date == date(2026, 4, 15)
        assert evs[0].old_pence == 1_099
        assert evs[0].new_pence == 1_299
        assert evs[0].delta_pence == 200

    def test_repeat_charge_at_new_price_does_not_refire(
            self, session: Session, sub: Subscription) -> None:
        subs.record_price(session, sub, date(2026, 4, 15), 1_299)
        subs.record_price(session, sub, date(2026, 5, 15), 1_299)
        subs.record_price(session, sub, date(2026, 6, 15), 1_299)
        assert len(_events(session, sub.id)) == 1

    def test_same_day_reimport_still_one_event(
            self, session: Session, sub: Subscription) -> None:
        subs.record_price(session, sub, date(2026, 4, 15), 1_299)
        subs.record_price(session, sub, date(2026, 4, 15), 1_299)
        assert len(_events(session, sub.id)) == 1

    def test_within_tolerance_no_event(
            self, session: Session, sub: Subscription) -> None:
        # 10% tolerance: £10.99 → ±£1.09 → £11.50 inside
        subs.record_price(session, sub, date(2026, 4, 15), 1_150)
        assert _events(session, sub.id) == []

    def test_boundary_exactly_at_tolerance_no_event(
            self, session: Session, household: Household) -> None:
        s = subs.create(
            session, household.id, payee="Exact",
            frequency=Frequency.monthly, expected_pence=1_000,
            next_renewal_date=date(2026, 3, 1), tolerance_pct=10)
        subs.record_price(session, s, date(2026, 3, 1), 1_100)  # =10%
        assert _events(session, s.id) == []
        # 1100 → 1200 is +9.1% — still inside tolerance, no event
        subs.record_price(session, s, date(2026, 4, 1), 1_200)
        assert _events(session, s.id) == []

    def test_just_over_tolerance_fires(
            self, session: Session, household: Household) -> None:
        s = subs.create(
            session, household.id, payee="JustOver",
            frequency=Frequency.monthly, expected_pence=1_000,
            next_renewal_date=date(2026, 3, 1), tolerance_pct=10)
        subs.record_price(session, s, date(2026, 3, 1), 1_101)  # 10.1%
        evs = _events(session, s.id)
        assert len(evs) == 1 and evs[0].new_pence == 1_101

    def test_price_drop_also_detected(
            self, session: Session, sub: Subscription) -> None:
        subs.record_price(session, sub, date(2026, 4, 15), 899)
        evs = _events(session, sub.id)
        assert len(evs) == 1 and evs[0].delta_pence == -200

    def test_variable_dd_never_produces_events(
            self, session: Session, household: Household) -> None:
        """Variable candidates can't be confirmed → no subscription →
        no price-change path at all."""
        a = Account(household_id=household.id, name="Joint",
                    type="joint", opening_date=date(2025, 1, 1))
        session.add(a)
        session.flush()
        dates = [date(2025, 9, 15), date(2025, 10, 15),
                 date(2025, 11, 15), date(2025, 12, 15),
                 date(2026, 1, 15)]
        for d, amt in zip(
                dates, [9_500, 14_200, 8_800, 16_100, 11_400],
                strict=True):
            session.add(Transaction(
                account_id=a.id, date=d,
                amount_pence=-amt, payee="Energy Co",
                status=TransactionStatus.cleared))
        session.flush()
        found = detection.scan(session, household.id)
        assert len(found) == 1
        assert found[0].candidate_type.value == "variable_dd"
        # cannot be promoted → no subscription → no price events
        assert _events(session, 1) == []


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_price_change_feed(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        sid = client.post("/api/v1/subscriptions", headers=h, json={
            "payee": "Streamflix", "frequency": "monthly",
            "expected": 1099, "next_renewal_date": "2026-03-15",
        }).json()["id"]
        client.post(f"/api/v1/subscriptions/{sid}/payments",
                    headers=h,
                    json={"date": "2026-03-15", "amount": 1099})
        client.post(f"/api/v1/subscriptions/{sid}/payments",
                    headers=h,
                    json={"date": "2026-04-15", "amount": 1299})
        feed = client.get("/api/v1/subscriptions/price-changes",
                          headers=h).json()
        assert len(feed) == 1
        assert feed[0]["delta_pence"] == 200
        per_sub = client.get(
            f"/api/v1/subscriptions/{sid}/price-changes",
            headers=h).json()
        assert per_sub == feed
