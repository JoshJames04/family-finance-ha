"""Issue #111 — materialised monthly aggregates + net worth."""
from __future__ import annotations

import tempfile
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import analytics, loans
from app.main import create_app
from app.persistence.models import (
    Account,
    Household,
    Loan,
    LoanDirection,
    MonthlyAggregate,
    Repayment,
    Transaction,
)


def _acct(session: Session, household: Household, opening: int = 0,
          name: str = "A") -> Account:
    a = Account(household_id=household.id, name=name, type="joint",
                opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    if opening:
        session.add(Transaction(account_id=a.id,
                                date=date(2025, 1, 1),
                                amount_pence=opening, payee="Opening"))
        session.flush()
    return a


def _txn(session: Session, acct: Account, amount: int,
         d: date) -> Transaction:
    t = Transaction(account_id=acct.id, date=d,
                    amount_pence=amount, payee="P")
    session.add(t)
    session.flush()
    return t


def _loan(session: Session, household: Household,
          direction: LoanDirection, principal: int,
          start: date = date(2025, 1, 1)) -> Loan:
    ln = Loan(household_id=household.id, party="Bank",
              direction=direction, principal_pence=principal,
              start_date=start, interest_bps_pa=0)
    session.add(ln)
    session.flush()
    return ln


class TestNetWorth:
    def test_golden_net_worth(
            self, session: Session, household: Household) -> None:
        """Hand-computed: 100k opening + 30k in − 20k out = 110k."""
        a = _acct(session, household, opening=100_000)
        _txn(session, a, 30_000, date(2025, 2, 5))
        _txn(session, a, -20_000, date(2025, 2, 20))
        assert analytics.net_worth_at(
            session, household.id, date(2025, 2, 28)) == 110_000

    def test_we_owe_loan_is_liability(
            self, session: Session, household: Household) -> None:
        _acct(session, household, opening=200_000)
        _loan(session, household, LoanDirection.we_owe, 50_000)
        assert analytics.net_worth_at(
            session, household.id, date(2025, 3, 1)) == 150_000

    def test_they_owe_loan_is_asset(
            self, session: Session, household: Household) -> None:
        _acct(session, household, opening=100_000)
        _loan(session, household, LoanDirection.they_owe, 25_000)
        assert analytics.net_worth_at(
            session, household.id, date(2025, 3, 1)) == 125_000

    def test_repayment_lifts_net_worth(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household, opening=200_000)
        ln = _loan(session, household, LoanDirection.we_owe, 50_000)
        tx = _txn(session, a, -10_000, date(2025, 2, 1))
        rp = Repayment(loan_id=ln.id, date=date(2025, 2, 1),
                       amount_pence=10_000)
        session.add(rp)
        session.flush()
        loans.link_repayment(session, rp, tx)
        # 200k − 10k repayment txn → 190k; loan 50k − 10k = 40k
        assert analytics.net_worth_at(
            session, household.id, date(2025, 3, 1)) == 150_000


class TestRebuild:
    def test_rebuild_then_read(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household, opening=100_000)
        _txn(session, a, 50_000, date(2025, 2, 5))
        _txn(session, a, -30_000, date(2025, 2, 20))
        _txn(session, a, -5_000, date(2025, 3, 3))
        n = analytics.rebuild(session, household.id)
        assert n == 3  # Jan, Feb, Mar
        rows = session.execute(select(MonthlyAggregate)).scalars().all()
        feb = next(r for r in rows if r.month == date(2025, 2, 1))
        assert feb.income_pence == 50_000
        assert feb.expense_pence == 30_000
        assert feb.savings_pence == 20_000
        # Feb end = opening 100k + 50k − 30k = 120k
        assert feb.net_worth_pence == 120_000

    def test_rebuild_idempotent(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household, opening=10_000)
        _txn(session, a, -1_000, date(2025, 2, 10))
        analytics.rebuild(session, household.id)
        snap1 = [(r.month, r.income_pence, r.expense_pence,
                  r.savings_pence, r.net_worth_pence)
                 for r in session.execute(
                     select(MonthlyAggregate)).scalars()]
        analytics.rebuild(session, household.id)
        snap2 = [(r.month, r.income_pence, r.expense_pence,
                  r.savings_pence, r.net_worth_pence)
                 for r in session.execute(
                     select(MonthlyAggregate)).scalars()]
        assert snap1 == snap2

    def test_deleted_txn_drops_month(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household, opening=10_000)
        t = _txn(session, a, -1_000, date(2025, 3, 10))
        analytics.rebuild(session, household.id)
        session.delete(t)
        session.flush()
        analytics.rebuild(session, household.id)
        months = [r.month for r in session.execute(
            select(MonthlyAggregate)).scalars()]
        assert date(2025, 3, 1) not in months

    def test_empty_household_rebuilds_zero(
            self, session: Session, household: Household) -> None:
        _acct(session, household)  # account but no transactions
        assert analytics.rebuild(session, household.id) == 0

    def test_household_scoped(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household, opening=10_000)
        _txn(session, a, -500, date(2025, 2, 2))
        other = Household(name="Other")
        session.add(other)
        session.flush()
        analytics.rebuild(session, household.id)
        analytics.rebuild(session, other.id)
        rows = session.execute(select(MonthlyAggregate).where(
            MonthlyAggregate.household_id == other.id)).scalars().all()
        assert rows == []


class TestSeriesAndApi:
    def test_series_chronological_and_capped(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household, opening=1_000)
        for m in (1, 2, 3, 4):
            _txn(session, a, -100, date(2025, m, 5))
        analytics.rebuild(session, household.id)
        rows = analytics.series(session, household.id, 3)
        assert [r.month for r in rows] == [
            date(2025, 2, 1), date(2025, 3, 1), date(2025, 4, 1)]

    @pytest.fixture()
    def client(self):
        d = tempfile.mkdtemp()
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": d}))) as c:
            yield c

    def test_api_rebuild_and_series(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "A"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", headers=h, json={
            "name": "A", "type": "joint", "opening_balance": 100_000,
            "opening_date": "2025-01-01"}).json()["id"]
        client.post("/api/v1/transactions", headers=h, json={
            "account_id": aid, "date": "2025-02-10",
            "amount": -25_000, "payee": "Shop"})
        r = client.post("/api/v1/analytics/rebuild", headers=h)
        assert r.status_code == 200
        assert r.json()["months_written"] == 2
        rows = client.get("/api/v1/analytics/monthly?months=12",
                          headers=h).json()
        feb = next(x for x in rows if x["month"] == "2025-02-01")
        assert feb["expense_pence"] == 25_000
        assert feb["net_worth_pence"] == 75_000
        nw = client.get("/api/v1/analytics/net-worth",
                        headers=h).json()
        assert nw["net_worth_pence"] == 75_000
