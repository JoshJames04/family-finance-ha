"""Budget lifecycle + API (issue #57).

Status transitions, frozen originals, locked-period read-only, overlap
without double-counting, per-line actuals.
"""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import budgets as dom
from app.domain.budgets import BudgetError
from app.main import create_app
from app.persistence.models import (
    Account,
    AccountType,
    BudgetLineSide,
    BudgetPeriodType,
    Category,
    ReportingPeriod,
    Transaction,
    TransactionStatus,
)


@pytest.fixture()
def category(session: Session, household) -> Category:
    c = Category(household_id=household.id, name="Food",
                 is_system=False, archived=False)
    session.add(c)
    session.flush()
    return c


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
    with TestClient(create_app(settings)) as c:
        yield c


def _mk_budget(session: Session, household) -> object:
    b = dom.create_budget(
        session, household.id, "May", BudgetPeriodType.monthly,
        date(2026, 5, 1), date(2026, 5, 31))
    return b


class TestDomain:
    def test_draft_then_approve_freezes_original(
            self, session: Session, household, category) -> None:
        b = _mk_budget(session, household)
        line = dom.add_line(session, b, category.id,
                            BudgetLineSide.expense, 50000)
        dom.approve(session, b)
        dom.revise_line(session, line, 55000)
        assert line.original_pence == 50000
        assert line.revised_pence == 55000
        assert dom.effective_pence(line) == 55000

    def test_no_lines_no_approve(self, session: Session, household):
        b = _mk_budget(session, household)
        with pytest.raises(BudgetError):
            dom.approve(session, b)

    def test_revise_in_draft_rejected(
            self, session: Session, household, category) -> None:
        b = _mk_budget(session, household)
        line = dom.add_line(session, b, category.id,
                            BudgetLineSide.expense, 50000)
        with pytest.raises(BudgetError):
            dom.revise_line(session, line, 1)

    def test_add_line_after_approve_rejected(
            self, session: Session, household, category) -> None:
        b = _mk_budget(session, household)
        dom.add_line(session, b, category.id,
                     BudgetLineSide.expense, 50000)
        dom.approve(session, b)
        with pytest.raises(BudgetError):
            dom.add_line(session, b, category.id,
                         BudgetLineSide.expense, 1)

    def test_locked_period_makes_read_only(
            self, session: Session, household, category) -> None:
        b = _mk_budget(session, household)
        line = dom.add_line(session, b, category.id,
                            BudgetLineSide.expense, 50000)
        dom.approve(session, b)
        # period locks AFTER the budget exists → budget goes read-only
        session.add(ReportingPeriod(
            household_id=household.id, label="2026-05",
            start_date=date(2026, 5, 1), end_date=date(2026, 5, 31),
            locked=True))
        session.flush()
        assert dom.is_read_only(session, b) is True
        with pytest.raises(BudgetError):
            dom.revise_line(session, line, 1)
        with pytest.raises(BudgetError):
            dom.add_line(session, b, category.id,
                         BudgetLineSide.expense, 1)
        # partial coverage does NOT lock
        b2 = dom.create_budget(
            session, household.id, "Jun", BudgetPeriodType.monthly,
            date(2026, 6, 1), date(2026, 6, 30))
        assert dom.is_read_only(session, b2) is False

    def test_overlap_no_double_count(
            self, session: Session, household, category) -> None:
        """A May monthly and a spanning custom budget each report the
        same underlying transaction — attribution is per-budget."""
        acct = Account(household_id=household.id, name="A",
                       type=AccountType.joint, currency="GBP",
                       opening_balance_pence=0,
                       opening_date=date(2026, 1, 1), archived=False)
        session.add(acct)
        session.flush()
        session.add(Transaction(
            account_id=acct.id, date=date(2026, 5, 10),
            amount_pence=-4000, payee="X", category_id=category.id,
            status=TransactionStatus.cleared))
        m = _mk_budget(session, household)
        lm = dom.add_line(session, m, category.id,
                          BudgetLineSide.expense, 50000)
        c = dom.create_budget(
            session, household.id, "Spring", BudgetPeriodType.custom,
            date(2026, 4, 1), date(2026, 6, 30))
        lc = dom.add_line(session, c, category.id,
                          BudgetLineSide.expense, 200000)
        assert dom.actuals_for_budget(session, m)[lm.id] == 4000
        assert dom.actuals_for_budget(session, c)[lc.id] == 4000

    def test_income_line_sign(self, session: Session, household,
                              category) -> None:
        acct = Account(household_id=household.id, name="A",
                       type=AccountType.joint, currency="GBP",
                       opening_balance_pence=0,
                       opening_date=date(2026, 1, 1), archived=False)
        session.add(acct)
        session.flush()
        session.add(Transaction(
            account_id=acct.id, date=date(2026, 5, 1),
            amount_pence=300000, payee="Wage",
            category_id=category.id,
            status=TransactionStatus.cleared))
        b = _mk_budget(session, household)
        line = dom.add_line(session, b, category.id,
                            BudgetLineSide.income, 280000)
        assert dom.actuals_for_budget(session, b)[line.id] == 300000


class TestApi:
    def test_full_lifecycle(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        cat = client.post("/api/v1/categories", json={"name": "Food"},
                          headers=h).json()
        b = client.post("/api/v1/budgets", json={
            "name": "May", "period_type": "monthly",
            "start_date": "2026-05-01", "end_date": "2026-05-31"},
            headers=h)
        assert b.status_code == 201
        bid = b.json()["id"]
        line = client.post(f"/api/v1/budgets/{bid}/lines", json={
            "category_id": cat["id"], "side": "expense",
            "amount": 50000}, headers=h)
        assert line.status_code == 201
        lid = line.json()["id"]
        assert line.json()["effective_pence"] == 50000

        assert client.post(f"/api/v1/budgets/{bid}/approve",
                           headers=h).status_code == 200
        detail = client.get(f"/api/v1/budgets/{bid}", headers=h).json()
        assert detail["status"] == "approved"
        assert detail["read_only"] is False

        r = client.patch(f"/api/v1/budgets/{bid}/lines/{lid}",
                         json={"amount": 55500}, headers=h)
        assert r.status_code == 200
        assert r.json()["original_pence"] == 50000
        assert r.json()["revised_pence"] == 55500

        assert client.post(f"/api/v1/budgets/{bid}/lock",
                           headers=h).status_code == 200
        assert client.patch(f"/api/v1/budgets/{bid}/lines/{lid}",
                            json={"amount": 1},
                            headers=h).status_code == 409
        act = client.get(f"/api/v1/budgets/{bid}/actuals",
                         headers=h).json()
        assert act["totals"] == {str(lid): 0}

    def test_scoping_and_validation(self, client: TestClient) -> None:
        h1 = client.post("/api/v1/households",
                         json={"name": "A"}).json()["id"]
        h2 = client.post("/api/v1/households",
                         json={"name": "B"}).json()["id"]
        b = client.post("/api/v1/budgets", json={
            "name": "May", "period_type": "monthly",
            "start_date": "2026-05-01", "end_date": "2026-05-31"},
            headers={"X-Household-Id": str(h1)})
        bid = b.json()["id"]
        bad = {"X-Household-Id": str(h2)}
        assert client.get(f"/api/v1/budgets/{bid}",
                          headers=bad).status_code == 404
        assert client.get("/api/v1/budgets",
                          headers=bad).json()["total"] == 0
        # inverted range → 409
        r = client.post("/api/v1/budgets", json={
            "name": "bad", "period_type": "custom",
            "start_date": "2026-06-01", "end_date": "2026-05-01"},
            headers={"X-Household-Id": str(h1)})
        assert r.status_code == 409
        # approving empty budget → 409
        assert client.post(f"/api/v1/budgets/{bid}/approve",
                           headers={"X-Household-Id": str(h1)}
                           ).status_code == 409
