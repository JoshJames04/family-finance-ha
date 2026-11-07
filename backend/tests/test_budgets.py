"""Budget lifecycle + API (issue #57).

Status transitions, frozen originals, locked-period read-only, overlap
without double-counting, per-line actuals.
"""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
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
    BudgetStatus,
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
        dom.revise_line(session, line, 55000, "higher food costs")
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
            dom.revise_line(session, line, 1, "r")

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
            dom.revise_line(session, line, 1, "r")
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
                         json={"amount": 55500, "reason": "food prices"}, headers=h)
        assert r.status_code == 200
        assert r.json()["original_pence"] == 50000
        assert r.json()["revised_pence"] == 55500

        assert client.post(f"/api/v1/budgets/{bid}/lock",
                           headers=h).status_code == 200
        assert client.patch(f"/api/v1/budgets/{bid}/lines/{lid}",
                            json={"amount": 1, "reason": "x"},
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


class TestTripleValue:
    """ADR-0007: original immutable, revised audited w/ reason, actual
    derived live — variance + accuracy computed (#58)."""

    def test_reason_required(self, session: Session, household,
                             category) -> None:
        b = _mk_budget(session, household)
        line = dom.add_line(session, b, category.id,
                            BudgetLineSide.expense, 50000)
        dom.approve(session, b)
        for bad in ("", "   "):
            with pytest.raises(BudgetError):
                dom.revise_line(session, line, 1, bad)

    def test_original_cannot_be_edited(
            self, session: Session, household, category) -> None:
        b = _mk_budget(session, household)
        line = dom.add_line(session, b, category.id,
                            BudgetLineSide.expense, 50000)
        dom.approve(session, b)
        dom.revise_line(session, line, 70000, "price rise")
        assert line.original_pence == 50000
        assert line.revision_reason == "price rise"

    def test_variance_and_accuracy(self, session: Session, household,
                                   category) -> None:
        acct = Account(household_id=household.id, name="A",
                       type=AccountType.joint, currency="GBP",
                       opening_balance_pence=0,
                       opening_date=date(2026, 1, 1), archived=False)
        session.add(acct)
        session.flush()
        session.add(Transaction(
            account_id=acct.id, date=date(2026, 5, 3),
            amount_pence=-40000, payee="X", category_id=category.id,
            status=TransactionStatus.cleared))
        b = _mk_budget(session, household)
        line = dom.add_line(session, b, category.id,
                            BudgetLineSide.expense, 50000)
        dom.approve(session, b)
        actual = dom.actuals_for_budget(session, b)[line.id]
        a = dom.line_analysis(actual, line)
        assert a["actual_pence"] == 40000
        assert a["variance_pence"] == 10000  # 50000 - 40000 underspent
        assert a["accuracy_pct"] == 80.0     # |50000-40000|/50000 = 20%
        dom.revise_line(session, line, 45000, "closer")
        a2 = dom.line_analysis(actual, line)
        assert a2["variance_pence"] == 5000
        assert a2["accuracy_pct"] == 88.89   # revision improved accuracy
        assert a2["original_accuracy_pct"] == 80.0

    def test_actuals_flow_through_without_refresh(
            self, session: Session, household, category) -> None:
        acct = Account(household_id=household.id, name="A",
                       type=AccountType.joint, currency="GBP",
                       opening_balance_pence=0,
                       opening_date=date(2026, 1, 1), archived=False)
        session.add(acct)
        session.flush()
        b = _mk_budget(session, household)
        line = dom.add_line(session, b, category.id,
                            BudgetLineSide.expense, 50000)
        assert dom.actuals_for_budget(session, b)[line.id] == 0
        session.add(Transaction(
            account_id=acct.id, date=date(2026, 5, 5),
            amount_pence=-9000, payee="X", category_id=category.id,
            status=TransactionStatus.cleared))
        session.flush()
        assert dom.actuals_for_budget(session, b)[line.id] == 9000

    def test_api_revision_audit_includes_reason(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        cat = client.post("/api/v1/categories", json={"name": "Food"},
                          headers=h).json()
        bid = client.post("/api/v1/budgets", json={
            "name": "May", "period_type": "monthly",
            "start_date": "2026-05-01", "end_date": "2026-05-31"},
            headers=h).json()["id"]
        lid = client.post(f"/api/v1/budgets/{bid}/lines", json={
            "category_id": cat["id"], "side": "expense",
            "amount": 50000}, headers=h).json()["id"]
        client.post(f"/api/v1/budgets/{bid}/approve", headers=h)
        # reason missing → 422
        r = client.patch(f"/api/v1/budgets/{bid}/lines/{lid}",
                         json={"amount": 1}, headers=h)
        assert r.status_code == 422
        client.patch(f"/api/v1/budgets/{bid}/lines/{lid}",
                     json={"amount": 60000, "reason": "energy rise"},
                     headers=h)
        from app.persistence import session as ps
        from app.persistence.models import AuditEvent
        assert ps._session_factory is not None
        with ps._session_factory() as s:
            ev = [e for e in s.query(AuditEvent).all()
                  if e.entity_type == "budget_line"
                  and e.entity_id == lid][-1]
        assert "energy rise" in (ev.payload or "")


class TestTemplatesAndCopy:
    """Issue #59 — template decoupling + copy-forward fidelity."""

    def test_template_apply_and_decouple(
            self, session: Session, household, category) -> None:
        t = dom.create_template(session, household.id, "Monthly")
        dom.add_template_line(session, t, category.id,
                              BudgetLineSide.expense, 40000)
        b = _mk_budget(session, household)
        created, warnings = dom.apply_template(session, b, t)
        assert len(created) == 1 and warnings == []
        assert b.lines[0].original_pence == 40000
        assert b.from_template_id == t.id
        # editing the template must not mutate the budget (AC)
        dom.update_template_line(session, t.lines[0], 999)
        session.refresh(b.lines[0])
        assert b.lines[0].original_pence == 40000

    def test_partial_application_skips_duplicates(
            self, session: Session, household, category) -> None:
        cat2 = Category(household_id=household.id, name="Travel",
                        is_system=False, archived=False)
        session.add(cat2)
        session.flush()
        t = dom.create_template(session, household.id, "T")
        dom.add_template_line(session, t, category.id,
                              BudgetLineSide.expense, 40000)
        dom.add_template_line(session, t, cat2.id,
                              BudgetLineSide.expense, 30000)
        b = _mk_budget(session, household)
        dom.add_line(session, b, category.id,
                     BudgetLineSide.expense, 50000)
        created, warnings = dom.apply_template(session, b, t)
        assert len(created) == 1
        assert created[0].category_id == cat2.id
        assert any("already a line" in w for w in warnings)

    def test_copy_forward_fidelity(
            self, session: Session, household, category) -> None:
        src = _mk_budget(session, household)
        line = dom.add_line(session, src, category.id,
                            BudgetLineSide.expense, 50000)
        dom.approve(session, src)
        dom.revise_line(session, line, 70000, "rise")
        new, warnings = dom.copy_forward(
            session, src, "June", date(2026, 6, 1), date(2026, 6, 30))
        assert warnings == []
        assert new.status == BudgetStatus.draft
        assert new.copied_from_id == src.id
        assert len(new.lines) == 1
        nl = new.lines[0]
        assert nl.original_pence == 50000     # original→original
        assert nl.revised_pence is None        # fresh cycle, no revision
        assert nl.category_id == category.id

    def test_copy_across_archived_category_warns(
            self, session: Session, household, category) -> None:
        src = _mk_budget(session, household)
        dom.add_line(session, src, category.id,
                     BudgetLineSide.expense, 50000)
        category.archived = True
        session.flush()
        new, warnings = dom.copy_forward(
            session, src, "June", date(2026, 6, 1), date(2026, 6, 30))
        assert len(new.lines) == 1  # cloned for history
        assert any("archived" in w for w in warnings)

    def test_apply_template_to_approved_rejected(
            self, session: Session, household, category) -> None:
        t = dom.create_template(session, household.id, "T")
        dom.add_template_line(session, t, category.id,
                              BudgetLineSide.expense, 1)
        b = _mk_budget(session, household)
        dom.add_line(session, b, category.id,
                     BudgetLineSide.expense, 50000)
        dom.approve(session, b)
        with pytest.raises(BudgetError):
            dom.apply_template(session, b, t)

    def test_api_template_flow(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        cat = client.post("/api/v1/categories", json={"name": "Food"},
                          headers=h).json()
        tid = client.post("/api/v1/budgets/templates",
                          json={"name": "Monthly"},
                          headers=h).json()["id"]
        client.post(f"/api/v1/budgets/templates/{tid}/lines", json={
            "category_id": cat["id"], "side": "expense",
            "amount": 40000}, headers=h)
        bid = client.post("/api/v1/budgets", json={
            "name": "May", "period_type": "monthly",
            "start_date": "2026-05-01", "end_date": "2026-05-31"},
            headers=h).json()["id"]
        r = client.post(f"/api/v1/budgets/{bid}/apply-template",
                        json={"template_id": tid}, headers=h)
        assert r.status_code == 200 and r.json()["created"] == 1
        c = client.post(f"/api/v1/budgets/{bid}/copy-forward", json={
            "name": "June", "start_date": "2026-06-01",
            "end_date": "2026-06-30"}, headers=h)
        assert c.status_code == 201
        assert c.json()["budget"]["status"] == "draft"
        d = client.get(f"/api/v1/budgets/{c.json()['budget']['id']}",
                       headers=h).json()
        assert d["lines"][0]["original_pence"] == 40000


class TestRollover:
    """Issue #60 — surplus/deficit carries, source never mutated."""

    def _setup(self, session: Session, household, category):
        acct = Account(household_id=household.id, name="A",
                       type=AccountType.joint, currency="GBP",
                       opening_balance_pence=0,
                       opening_date=date(2026, 1, 1), archived=False)
        session.add(acct)
        session.flush()
        session.add(Transaction(
            account_id=acct.id, date=date(2026, 5, 10),
            amount_pence=-46000, payee="X", category_id=category.id,
            status=TransactionStatus.cleared))
        session.flush()
        may = _mk_budget(session, household)
        lm = dom.add_line(session, may, category.id,
                          BudgetLineSide.expense, 50000)
        lm.rollover = True
        dom.approve(session, may)
        june = dom.create_budget(
            session, household.id, "June", BudgetPeriodType.monthly,
            date(2026, 6, 1), date(2026, 6, 30))
        lj = dom.add_line(session, june, category.id,
                          BudgetLineSide.expense, 50000)
        return may, june, lm, lj

    def test_surplus_carry(self, session: Session, household,
                           category) -> None:
        may, june, lm, lj = self._setup(session, household, category)
        # spent 46000 of 50000 → carry +4000 into June revised
        events = dom.apply_rollover(session, may, june)
        assert events[0]["carry_pence"] == 4000
        assert lj.revised_pence == 54000
        assert "rollover" in (lj.revision_reason or "")
        # source untouched
        assert may.status == BudgetStatus.approved
        assert lm.original_pence == 50000

    def test_deficit_carry(self, session: Session, household,
                           category) -> None:
        may, june, lm, lj = self._setup(session, household, category)
        # overspend: effective 50000, actual 56000 → carry −6000
        acct = session.execute(
            select(Account)).scalar_one()
        session.add(Transaction(
            account_id=acct.id, date=date(2026, 5, 20),
            amount_pence=-10000, payee="Y", category_id=category.id,
            status=TransactionStatus.cleared))
        session.flush()
        dom.apply_rollover(session, may, june)
        assert lj.revised_pence == 44000

    def test_opt_out_lines_ignored(self, session: Session, household,
                                   category) -> None:
        may, june, lm, lj = self._setup(session, household, category)
        lm.rollover = False
        events = dom.apply_rollover(session, may, june)
        assert events == [] and lj.revised_pence is None

    def test_draft_source_rejected(self, session: Session, household,
                                   category) -> None:
        may, june, lm, lj = self._setup(session, household, category)
        may.status = BudgetStatus.draft
        with pytest.raises(BudgetError):
            dom.apply_rollover(session, may, june)

    def test_rollover_over_locked_source_boundary(
            self, session: Session, household, category) -> None:
        may, june, lm, lj = self._setup(session, household, category)
        session.add(ReportingPeriod(
            household_id=household.id, label="2026-05",
            start_date=date(2026, 5, 1), end_date=date(2026, 5, 31),
            locked=True))
        session.flush()
        # source locked is fine — rollover only reads it
        events = dom.apply_rollover(session, may, june)
        assert events[0]["carry_pence"] == 4000

    def test_locked_target_rejected(self, session: Session, household,
                                    category) -> None:
        may, june, lm, lj = self._setup(session, household, category)
        dom.approve(session, june)
        dom.lock(session, june)
        with pytest.raises(BudgetError):
            dom.apply_rollover(session, may, june)

    def test_api_rollover(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        cat = client.post("/api/v1/categories", json={"name": "Food"},
                          headers=h).json()
        b1 = client.post("/api/v1/budgets", json={
            "name": "May", "period_type": "monthly",
            "start_date": "2026-05-01", "end_date": "2026-05-31"},
            headers=h).json()["id"]
        client.post(f"/api/v1/budgets/{b1}/lines", json={
            "category_id": cat["id"], "side": "expense",
            "amount": 50000, "rollover": True}, headers=h)
        client.post(f"/api/v1/budgets/{b1}/approve", headers=h)
        b2 = client.post("/api/v1/budgets", json={
            "name": "June", "period_type": "monthly",
            "start_date": "2026-06-01", "end_date": "2026-06-30"},
            headers=h).json()["id"]
        client.post(f"/api/v1/budgets/{b2}/lines", json={
            "category_id": cat["id"], "side": "expense",
            "amount": 50000}, headers=h)
        r = client.post(f"/api/v1/budgets/{b2}/rollover",
                        json={"source_budget_id": b1}, headers=h)
        assert r.status_code == 200 and r.json()["applied"] == 1
        d = client.get(f"/api/v1/budgets/{b2}", headers=h).json()
        # nothing spent → full 50000 surplus carries onto original
        assert d["lines"][0]["revised_pence"] == 100000


class TestMemberScoping:
    """Issue #61 — private vs shared budgets enforced at the API."""

    def _setup(self, client: TestClient):
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        pa = client.post("/api/v1/profiles", json={
            "display_name": "Bob", "role": "member"},
            headers=h).json()["id"]
        pb = client.post("/api/v1/profiles", json={
            "display_name": "Blair", "role": "member"},
            headers=h).json()["id"]
        admin = client.post("/api/v1/profiles", json={
            "display_name": "Root", "role": "admin"},
            headers=h).json()["id"]
        return hid, pa, pb, admin

    def _budget(self, client, hid, owner, name="B"):
        return client.post("/api/v1/budgets", json={
            "name": name, "period_type": "monthly",
            "start_date": "2026-05-01", "end_date": "2026-05-31",
            "owner_profile_id": owner},
            headers={"X-Household-Id": str(hid)}).json()["id"]

    def test_private_invisible_to_other_member(
            self, client: TestClient) -> None:
        hid, pa, pb, _ = self._setup(client)
        bid = self._budget(client, hid, pa)
        h_b = {"X-Household-Id": str(hid), "X-Profile-Id": str(pb)}
        assert client.get(f"/api/v1/budgets/{bid}",
                          headers=h_b).status_code == 404
        # owner sees it
        h_a = {"X-Household-Id": str(hid), "X-Profile-Id": str(pa)}
        assert client.get(f"/api/v1/budgets/{bid}",
                          headers=h_a).status_code == 200

    def test_list_scoped_per_member(self, client: TestClient) -> None:
        hid, pa, pb, _ = self._setup(client)
        self._budget(client, hid, pa, "PrivA")
        self._budget(client, hid, pb, "PrivB")
        self._budget(client, hid, None, "Shared")
        h_b = {"X-Household-Id": str(hid), "X-Profile-Id": str(pb)}
        names = {b["name"] for b in
                 client.get("/api/v1/budgets", headers=h_b
                            ).json()["items"]}
        assert names == {"PrivB", "Shared"}  # B's own + shared, not A's
        # anonymous → shared only
        anon = {"X-Household-Id": str(hid)}
        names = {b["name"] for b in
                 client.get("/api/v1/budgets", headers=anon
                            ).json()["items"]}
        assert names == {"Shared"}
        # household aggregate view
        names = {b["name"] for b in client.get(
            "/api/v1/budgets", headers=anon,
            params={"shared_only": "true"}).json()["items"]}
        assert names == {"Shared"}

    def test_admin_sees_all(self, client: TestClient) -> None:
        hid, pa, _, admin = self._setup(client)
        bid = self._budget(client, hid, pa)
        h_ad = {"X-Household-Id": str(hid), "X-Profile-Id": str(admin)}
        assert client.get(f"/api/v1/budgets/{bid}",
                          headers=h_ad).status_code == 200

    def test_share_conversion_audited(self, client: TestClient) -> None:
        hid, pa, pb, _ = self._setup(client)
        bid = self._budget(client, hid, pa)
        h_b = {"X-Household-Id": str(hid), "X-Profile-Id": str(pb)}
        # B cannot share A's budget
        assert client.post(f"/api/v1/budgets/{bid}/share",
                           headers=h_b).status_code == 404
        h_a = {"X-Household-Id": str(hid), "X-Profile-Id": str(pa)}
        r = client.post(f"/api/v1/budgets/{bid}/share", headers=h_a)
        assert r.status_code == 200
        # now visible to B
        assert client.get(f"/api/v1/budgets/{bid}",
                          headers=h_b).status_code == 200
        from app.persistence import session as ps
        from app.persistence.models import AuditEvent
        with ps._session_factory() as s:
            ev = [e for e in s.query(AuditEvent).all()
                  if e.entity_type == "budget" and e.entity_id == bid]
        assert '"shared": true' in ev[-1].payload

    def test_mutations_scoped(self, client: TestClient) -> None:
        hid, pa, pb, _ = self._setup(client)
        bid = self._budget(client, hid, pa)
        cat = client.post("/api/v1/categories", json={"name": "F"},
                          headers={"X-Household-Id": str(hid)}).json()
        h_b = {"X-Household-Id": str(hid), "X-Profile-Id": str(pb)}
        assert client.post(f"/api/v1/budgets/{bid}/lines", json={
            "category_id": cat["id"], "side": "expense",
            "amount": 100}, headers=h_b).status_code == 404
        assert client.post(f"/api/v1/budgets/{bid}/approve",
                           headers=h_b).status_code == 404

    def test_deactivated_profile_403(self, client: TestClient) -> None:
        hid, pa, _, _ = self._setup(client)
        from app.persistence import session as ps
        from app.persistence.models import UserProfile
        with ps._session_factory() as s:
            s.get(UserProfile, pa).is_active = False
            s.commit()
        r = client.get("/api/v1/budgets", headers={
            "X-Household-Id": str(hid), "X-Profile-Id": str(pa)})
        assert r.status_code == 403
