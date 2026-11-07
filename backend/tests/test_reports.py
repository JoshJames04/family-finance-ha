"""Issue #110 — deterministic reports + drill-through integrity."""
from __future__ import annotations

import tempfile
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import reports
from app.main import create_app
from app.persistence.models import (
    Account,
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    Category,
    Household,
    Transaction,
    TransactionStatus,
    Transfer,
)


def _acct(session: Session, household: Household, name: str = "A") -> Account:
    a = Account(household_id=household.id, name=name, type="joint",
                opening_balance_pence=0, opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _cat(session: Session, household: Household, name: str) -> Category:
    c = Category(household_id=household.id, name=name)
    session.add(c)
    session.flush()
    return c


def _txn(session: Session, acct: Account, amount: int,
         day: int = 10, month: int = 2, year: int = 2025,
         category_id: int | None = None, transfer_id: int | None = None,
         payee: str = "P") -> Transaction:
    if transfer_id is not None and session.get(
            Transfer, transfer_id) is None:
        session.add(Transfer(id=transfer_id,
                             household_id=acct.household_id))
        session.flush()
    t = Transaction(account_id=acct.id, date=date(year, month, day),
                    amount_pence=amount, payee=payee,
                    category_id=category_id, transfer_id=transfer_id)
    session.add(t)
    session.flush()
    return t


def _figs(doc: dict, section: str) -> dict[str, dict]:
    s = next(x for x in doc["sections"] if x["key"] == section)
    return {f["key"]: f for f in s["figures"]}


class TestFigures:
    def test_income_expense_transfers_split(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household)
        b = _acct(session, household, "B")
        food = _cat(session, household, "Food")
        _txn(session, a, 300_000)                     # salary in
        _txn(session, a, -50_000, category_id=food.id)
        _txn(session, a, -20_000)                     # uncategorised
        _txn(session, a, -10_000, transfer_id=1)      # internal out-leg
        _txn(session, b, 10_000, transfer_id=1)       # internal in-leg
        doc = reports.build_report(
            session, household.id, date(2025, 2, 1), date(2025, 2, 28))
        assert _figs(doc, "income")["income_total"]["amount_pence"] \
            == 300_000
        spend = _figs(doc, "spend_by_category")
        # the £100 internal leg is NOT spend
        assert spend[f"category_{food.id}"]["amount_pence"] == 50_000
        assert spend["uncategorised"]["amount_pence"] == 20_000
        assert _figs(doc, "transfers")["transfer_total"][
            "amount_pence"] == 10_000
        assert _figs(doc, "transfers")["transfer_count"]["count"] == 1
        savings = _figs(doc, "savings")
        assert savings["savings_pence"]["amount_pence"] == 230_000
        assert savings["savings_rate_bps"]["amount_pence"] == round(
            230_000 * 10_000 / 300_000)

    def test_window_is_inclusive(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household)
        _txn(session, a, -100, day=1)
        _txn(session, a, -200, day=28)
        _txn(session, a, -300, day=1, month=3)
        doc = reports.build_report(
            session, household.id, date(2025, 2, 1), date(2025, 2, 28))
        assert _figs(doc, "spend_by_category")[
            "uncategorised"]["amount_pence"] == 300

    def test_void_transactions_excluded(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household)
        t = _txn(session, a, -999)
        t.status = TransactionStatus.void
        session.flush()
        doc = reports.build_report(
            session, household.id, date(2025, 2, 1), date(2025, 2, 28))
        assert _figs(doc, "spend_by_category") == {}


class TestDeterminism:
    def test_byte_identical_rerun(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household)
        for i in range(5):
            _txn(session, a, -(i + 1) * 1000, day=i + 1)
        d1 = reports.build_report(
            session, household.id, date(2025, 2, 1), date(2025, 2, 28))
        d2 = reports.build_report(
            session, household.id, date(2025, 2, 1), date(2025, 2, 28))
        assert d1 == d2

    def test_category_order_deterministic(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household)
        c2 = _cat(session, household, "Zed")
        c1 = _cat(session, household, "Alpha")
        _txn(session, a, -100, category_id=c2.id)
        _txn(session, a, -200, category_id=c1.id)
        _txn(session, a, -50)  # uncategorised sorts last
        doc = reports.build_report(
            session, household.id, date(2025, 2, 1), date(2025, 2, 28))
        keys = [f["key"] for f in _figs(
            doc, "spend_by_category").values()]
        assert keys == sorted(
            keys, key=lambda k: (k == "uncategorised", k))


class TestPrior:
    def test_yoy_deltas(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household)
        _txn(session, a, 200_000, year=2025)
        _txn(session, a, 150_000, year=2024)
        _txn(session, a, -10_000, year=2025)  # new category this year
        doc = reports.build_report(
            session, household.id, date(2025, 1, 1), date(2025, 12, 31),
            prior=(date(2024, 1, 1), date(2024, 12, 31)))
        inc = _figs(doc, "income")["income_total"]
        assert inc["amount_pence"] == 200_000
        assert inc["prior_amount_pence"] == 150_000
        assert inc["delta_pence"] == 50_000
        assert doc["prior_period"]["start"] == "2024-01-01"

    def test_prior_only_figure_appears(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household)
        c = _cat(session, household, "Gone")
        _txn(session, a, -5_000, year=2024, category_id=c.id)
        doc = reports.build_report(
            session, household.id, date(2025, 1, 1), date(2025, 12, 31),
            prior=(date(2024, 1, 1), date(2024, 12, 31)))
        f = _figs(doc, "spend_by_category")[f"category_{c.id}"]
        assert f["amount_pence"] is None
        assert f["prior_amount_pence"] == 5_000


class TestVariances:
    def test_contained_budget_lines(
            self, session: Session, household: Household) -> None:
        a = _acct(session, household)
        food = _cat(session, household, "Food")
        b = Budget(household_id=household.id, name="Feb",
                   period_type=BudgetPeriodType.monthly,
                   start_date=date(2025, 2, 1), end_date=date(2025, 2, 28),
                   status=BudgetStatus.approved)
        session.add(b)
        session.flush()
        session.add(BudgetLine(budget_id=b.id, category_id=food.id,
                               side=BudgetLineSide.expense,
                               original_pence=60_000))
        session.flush()
        _txn(session, a, -45_000, category_id=food.id)
        doc = reports.build_report(
            session, household.id, date(2025, 2, 1), date(2025, 2, 28))
        figs = _figs(doc, "variances")
        (line,) = [f for k, f in figs.items()
                   if k.startswith(f"budget_{b.id}_line_")]
        # planned 60k - actual 45k → 15k under budget
        assert line["amount_pence"] == 15_000

    def test_overlapping_not_contained_budget_skipped(
            self, session: Session, household: Household) -> None:
        b = Budget(household_id=household.id, name="Q1",
                   period_type=BudgetPeriodType.monthly,
                   start_date=date(2025, 1, 15), end_date=date(2025, 2, 15),
                   status=BudgetStatus.approved)
        session.add(b)
        session.flush()
        doc = reports.build_report(
            session, household.id, date(2025, 2, 1), date(2025, 2, 28))
        assert _figs(doc, "variances") == {}


class TestApi:
    @pytest.fixture()
    def client(self):
        d = tempfile.mkdtemp()
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": d}))) as c:
            yield c

    def _seed(self, client: TestClient) -> dict[str, str]:
        hid = client.post("/api/v1/households",
                          json={"name": "R"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", headers=h, json={
            "name": "A", "type": "joint", "opening_balance": 0,
            "opening_date": "2025-01-01"}).json()["id"]
        client.post("/api/v1/transactions", headers=h, json={
            "account_id": aid, "date": "2025-02-10",
            "amount": 100_000, "payee": "Work"})
        client.post("/api/v1/transactions", headers=h, json={
            "account_id": aid, "date": "2025-02-12",
            "amount": -25_000, "payee": "Shop"})
        return h

    def test_monthly_report(self, client: TestClient) -> None:
        h = self._seed(client)
        r = client.get("/api/v1/reports/monthly?year=2025&month=2",
                       headers=h)
        assert r.status_code == 200
        doc = r.json()
        assert doc["period"] == {"start": "2025-02-01",
                                "end": "2025-02-28"}
        inc = next(f for s in doc["sections"]
                   for f in s["figures"] if f["key"] == "income_total")
        assert inc["amount_pence"] == 100_000

    def test_drill_through_replays_figure(
            self, client: TestClient) -> None:
        """AC: every figure's drill replays to the same total."""
        h = self._seed(client)
        doc = client.get(
            "/api/v1/reports/monthly?year=2025&month=2",
            headers=h).json()
        for s in doc["sections"]:
            for f in s["figures"]:
                if f.get("drill"):
                    items = client.get(
                        f["drill"]["endpoint"], headers=h,
                        params=f["drill"]["params"]).json()["items"]
                    replayed = sum(abs(i["amount_pence"])
                                   for i in items)
                    if f["amount_pence"] is not None:
                        assert replayed == abs(f["amount_pence"]), \
                            f"{f['key']} drill mismatch"
                comps = f.get("components") or []
                if comps and f["key"] == "savings_pence":
                    signed = sum(
                        i["amount_pence"]
                        for d in comps
                        for i in client.get(
                            d["endpoint"], headers=h,
                            params=d["params"]).json()["items"])
                    assert signed == f["amount_pence"], \
                        "savings components mismatch"

    def test_rerun_byte_identical(self, client: TestClient) -> None:
        h = self._seed(client)
        r1 = client.get("/api/v1/reports/annual?year=2025", headers=h)
        r2 = client.get("/api/v1/reports/annual?year=2025", headers=h)
        assert r1.content == r2.content

    def test_yoy_endpoint(self, client: TestClient) -> None:
        h = self._seed(client)
        r = client.get("/api/v1/reports/yoy?year=2025", headers=h)
        assert r.status_code == 200
        assert r.json()["prior_period"]["start"] == "2024-01-01"

    def test_household_scoped(self, client: TestClient) -> None:
        self._seed(client)
        hid2 = client.post("/api/v1/households",
                           json={"name": "Other"}).json()["id"]
        doc = client.get(
            "/api/v1/reports/monthly?year=2025&month=2",
            headers={"X-Household-Id": str(hid2)}).json()
        inc = next(f for s in doc["sections"]
                   for f in s["figures"] if f["key"] == "income_total")
        assert inc["amount_pence"] == 0
