"""Issue #113 — explainable expenditure forecasting.

ACs: every row names method + input window; a seasonal fixture
produces a visibly seasonal forecast; confirmed vs assumption vs
estimate separated.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import forecasting
from app.main import create_app
from app.persistence.models import (
    Account,
    Category,
    Frequency,
    Household,
    RecurringCommitment,
    Transaction,
    TransactionStatus,
    Transfer,
)

ANCHOR = date(2025, 1, 1)        # forecast starts Jan 2025
HISTORY = [date(2024, m, 15) for m in range(1, 13)]


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Current",
                type="personal", opening_balance_pence=0,
                opening_date=date(2024, 1, 1))
    session.add(a)
    session.flush()
    return a


@pytest.fixture()
def cat(session: Session, household: Household) -> Category:
    c = Category(household_id=household.id, name="Groceries")
    session.add(c)
    session.flush()
    return c


def _spend(session: Session, a: Account, on: date, pence: int,
           category_id: int | None = None,
           transfer_id: int | None = None) -> None:
    session.add(Transaction(
        account_id=a.id, date=on, amount_pence=-pence, payee="shop",
        status=TransactionStatus.cleared, source="manual",
        category_id=category_id, transfer_id=transfer_id))
    session.flush()


class TestModelledForecast:
    def test_seasonal_fixture_produces_seasonal_forecast(
            self, session: Session, household: Household,
            account: Account, cat: Category) -> None:
        """AC: December at 3× normal spend must forecast visibly
        higher than a normal month."""
        for d in HISTORY:
            _spend(session, account, d,
                   30_000 if d.month == 12 else 10_000, cat.id)
        rows = forecasting.forecast(
            session, household.id, from_month=ANCHOR, months=12,
            history_months=12)
        by_month = {(r.month.month, r.method): r for r in rows}
        dec = by_month[(12, "seasonal_index")]
        jan = by_month[(1, "seasonal_index")]
        assert dec.amount_pence > 2 * jan.amount_pence
        assert dec.confidence == "assumption"

    def test_rows_name_method_and_window(
            self, session: Session, household: Household,
            account: Account, cat: Category) -> None:
        """AC: provenance — method + input window on every row."""
        for d in HISTORY[:6]:
            _spend(session, account, d, 10_000, cat.id)
        rows = forecasting.forecast(
            session, household.id, from_month=ANCHOR, months=3,
            history_months=12)
        assert rows
        for r in rows:
            assert r.method in (
                "commitment", "seasonal_index", "trailing_average")
            assert r.confidence in (
                "confirmed", "assumption", "estimate")
            assert r.input_start == date(2024, 1, 1)
            assert r.input_end == date(2024, 12, 1)

    def test_thin_history_is_estimate(
            self, session: Session, household: Household,
            account: Account, cat: Category) -> None:
        for d in HISTORY[:2]:
            _spend(session, account, d, 10_000, cat.id)
        rows = forecasting.forecast(
            session, household.id, from_month=ANCHOR, months=2,
            history_months=12)
        assert rows
        assert all(r.method == "trailing_average" for r in rows)
        assert all(r.confidence == "estimate" for r in rows)

    def test_transfer_legs_not_spend(
            self, session: Session, household: Household,
            account: Account, cat: Category) -> None:
        """Internal transfer legs must never count as expenditure."""
        t = Transfer(household_id=household.id)
        session.add(t)
        session.flush()
        for d in HISTORY[:6]:
            _spend(session, account, d, 10_000, cat.id)
            _spend(session, account, d, 90_000, cat.id,
                   transfer_id=t.id)
        rows = forecasting.forecast(
            session, household.id, from_month=ANCHOR, months=1,
            history_months=12)
        modelled = [r for r in rows if r.method != "commitment"]
        assert modelled
        # Mean of six £100 months spread over a 12-month window.
        assert max(r.amount_pence for r in modelled) <= 10_000

    def test_deterministic(
            self, session: Session, household: Household,
            account: Account, cat: Category) -> None:
        for d in HISTORY:
            _spend(session, account, d, 10_000, cat.id)
        r1 = forecasting.forecast(
            session, household.id, from_month=ANCHOR, months=6)
        r2 = forecasting.forecast(
            session, household.id, from_month=ANCHOR, months=6)
        assert r1 == r2


class TestConfirmedLayer:
    def _commitment(self, session: Session, household: Household,
                    account: Account, cat: Category,
                    next_date: date, pence: int = -5_000
                    ) -> RecurringCommitment:
        c = RecurringCommitment(
            household_id=household.id, account_id=account.id,
            name="Rent", category_id=cat.id, amount_pence=pence,
            frequency=Frequency.monthly, next_date=next_date)
        session.add(c)
        session.flush()
        return c

    def test_commitment_emits_confirmed_rows(
            self, session: Session, household: Household,
            account: Account, cat: Category) -> None:
        self._commitment(session, household, account, cat,
                         date(2025, 1, 5))
        for d in HISTORY:
            _spend(session, account, d, 20_000, cat.id)
        rows = forecasting.forecast(
            session, household.id, from_month=ANCHOR, months=3)
        conf_rows = [r for r in rows if r.confidence == "confirmed"]
        assert conf_rows
        assert all(r.method == "commitment" for r in conf_rows)
        assert all(r.amount_pence == 5_000 for r in conf_rows)
        # Residual modelled spend sits beside the confirmed slice —
        # separated, not blended.
        resid = [r for r in rows if r.confidence != "confirmed"]
        assert resid
        assert all(r.confidence in ("assumption", "estimate")
                   for r in resid)

    def test_commitment_exceeds_model(
            self, session: Session, household: Household,
            account: Account, cat: Category) -> None:
        """Committed spend above the modelled average: residual
        floors at zero, confirmed row stands alone."""
        self._commitment(session, household, account, cat,
                         date(2025, 1, 5), pence=-50_000)
        for d in HISTORY[:6]:
            _spend(session, account, d, 5_000, cat.id)
        rows = forecasting.forecast(
            session, household.id, from_month=ANCHOR, months=1)
        assert all(r.amount_pence >= 0 for r in rows)
        assert [r.amount_pence for r in rows
                if r.confidence == "confirmed"] == [50_000]

    def test_uncategorised_commitment_ignored(
            self, session: Session, household: Household,
            account: Account) -> None:
        c = RecurringCommitment(
            household_id=household.id, account_id=account.id,
            name="Misc", category_id=None, amount_pence=-5_000,
            frequency=Frequency.monthly, next_date=date(2025, 1, 5))
        session.add(c)
        session.flush()
        rows = forecasting.forecast(
            session, household.id, from_month=ANCHOR, months=2)
        assert not any(r.confidence == "confirmed" for r in rows)


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_forecast_endpoint(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", json={
            "name": "Current", "type": "personal",
            "opening_balance": 100_000,
            "opening_date": "2024-01-01"}, headers=h).json()["id"]
        client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2024-11-15",
            "amount": -10_000, "payee": "shop"}, headers=h)
        res = client.get("/api/v1/analytics/forecast",
                         params={"months": 2}, headers=h)
        assert res.status_code == 200
        rows = res.json()
        for r in rows:
            assert r["method"] and r["confidence"]
            assert r["input_months"] >= 0
