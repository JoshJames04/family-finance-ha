"""Issue #93 — loans: outstanding derived from records, never stored;
schedule-vs-actual drift; repayment↔transaction links."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import loans
from app.domain.loans import LoanError
from app.main import create_app
from app.persistence.models import (
    Account,
    Household,
    Loan,
    LoanDirection,
    Repayment,
    Transaction,
    TransactionStatus,
)

TODAY = date(2025, 4, 10)


@pytest.fixture()
def loan(session: Session, household: Household) -> Loan:
    ln = Loan(household_id=household.id, party="Mum",
              direction=LoanDirection.we_owe,
              principal_pence=500_000,
              start_date=date(2025, 1, 1),
              expected_payment_pence=10_000, active=True)
    session.add(ln)
    session.flush()
    return ln


def _repay(session: Session, ln: Loan, pence: int,
           on: date) -> Repayment:
    r = Repayment(loan_id=ln.id, date=on, amount_pence=pence)
    session.add(r)
    session.flush()
    return r


class TestOutstanding:
    def test_principal_only(self, session: Session,
                            loan: Loan) -> None:
        assert loans.outstanding(session, loan, TODAY) == 500_000

    def test_repayment_reduces_outstanding(
            self, session: Session, loan: Loan) -> None:
        _repay(session, loan, 10_000, date(2025, 2, 1))
        _repay(session, loan, 10_000, date(2025, 3, 1))
        assert loans.outstanding(session, loan, TODAY) == 480_000

    def test_removal_recomputes(self, session: Session,
                              loan: Loan) -> None:
        """AC: outstanding recomputes after any repayment add/remove."""
        r = _repay(session, loan, 10_000, date(2025, 2, 1))
        assert loans.outstanding(session, loan, TODAY) == 490_000
        session.delete(r)
        session.flush()
        assert loans.outstanding(session, loan, TODAY) == 500_000

    def test_floored_at_zero(self, session: Session,
                             loan: Loan) -> None:
        _repay(session, loan, 600_000, date(2025, 2, 1))
        assert loans.outstanding(session, loan, TODAY) == 0

    def test_simple_interest_accrues(
            self, session: Session, household: Household) -> None:
        ln = Loan(household_id=household.id, party="Bank",
                  direction=LoanDirection.we_owe,
                  principal_pence=365_000,
                  start_date=date(2025, 1, 1),
                  interest_bps_pa=1000, active=True)  # 10% p.a.
        session.add(ln)
        session.flush()
        # 100 days → 365000 × 0.10 × 100/365 = 10_000
        on = date(2025, 1, 1) + __import__("datetime").timedelta(
            days=100)
        assert loans.accrued_interest(ln, on) == 10_000
        assert loans.outstanding(session, ln, on) == 375_000

    def test_no_interest_config_no_accrual(
            self, session: Session, loan: Loan) -> None:
        assert loans.accrued_interest(loan, TODAY) == 0


class TestDrift:
    def test_behind_schedule(self, session: Session,
                             loan: Loan) -> None:
        """Jan→Apr = 3 months expected (£300); £100 paid → £200 drift."""
        _repay(session, loan, 10_000, date(2025, 2, 1))
        d = loans.schedule_drift(session, loan, TODAY)
        assert d.expected_pence == 30_000
        assert d.actual_pence == 10_000
        assert d.drift_pence == 20_000

    def test_ahead_schedule_negative_drift(
            self, session: Session, loan: Loan) -> None:
        _repay(session, loan, 50_000, date(2025, 2, 1))
        assert loans.schedule_drift(
            session, loan, TODAY).drift_pence == -20_000

    def test_no_schedule_zero_drift(self, session: Session,
                                  household: Household) -> None:
        ln = Loan(household_id=household.id, party="Dad",
                  direction=LoanDirection.they_owe,
                  principal_pence=100_000,
                  start_date=date(2025, 1, 1), active=True)
        session.add(ln)
        session.flush()
        assert loans.schedule_drift(
            session, ln, TODAY).expected_pence == 0


class TestLink:
    def test_link_to_transaction(self, session: Session,
                                 household: Household,
                                 loan: Loan) -> None:
        a = Account(household_id=household.id, name="Joint",
                    type="joint", opening_date=date(2025, 1, 1))
        session.add(a)
        session.flush()
        tx = Transaction(account_id=a.id, date=date(2025, 2, 1),
                         amount_pence=-10_000, payee="Loan to Mum",
                         status=TransactionStatus.cleared,
                         source="manual")
        session.add(tx)
        session.flush()
        r = _repay(session, loan, 10_000, date(2025, 2, 1))
        loans.link_repayment(session, r, tx)
        assert r.transaction_id == tx.id

    def test_cross_household_link_rejected(
            self, session: Session, household: Household,
            loan: Loan) -> None:
        other = Household(name="Other")
        session.add(other)
        session.flush()
        a = Account(household_id=other.id, name="X",
                    type="joint", opening_date=date(2025, 1, 1))
        session.add(a)
        session.flush()
        tx = Transaction(account_id=a.id, date=date(2025, 2, 1),
                         amount_pence=-10_000, payee="T",
                         status=TransactionStatus.cleared,
                         source="manual")
        session.add(tx)
        session.flush()
        r = _repay(session, loan, 10_000, date(2025, 2, 1))
        with pytest.raises(LoanError):
            loans.link_repayment(session, r, tx)


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def test_loan_position_flow(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        ln = client.post("/api/v1/loans", headers=h, json={
            "party": "Mum", "direction": "we_owe",
            "principal": 500000, "start_date": "2025-01-01",
            "expected_payment": 10000})
        assert ln.status_code == 201, ln.json()
        lid = ln.json()["id"]
        rp = client.post(f"/api/v1/loans/{lid}/repayments", headers=h,
                         json={"date": "2025-02-01", "amount": 10000})
        assert rp.status_code == 201
        pos = client.get(f"/api/v1/loans/{lid}", headers=h).json()
        assert pos["outstanding_pence"] == 490_000
        assert pos["repaid_pence"] == 10_000
        # remove the repayment → outstanding recomputes
        rid = rp.json()["id"]
        client.delete(f"/api/v1/loans/{lid}/repayments/{rid}",
                      headers=h)
        assert client.get(f"/api/v1/loans/{lid}",
                          headers=h).json()["outstanding_pence"] \
            == 500_000

    def test_owner_round_trips(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        p = client.post("/api/v1/profiles", headers=h,
                        json={"display_name": "Josh"})
        assert p.status_code == 201, p.json()
        pid = p.json()["id"]
        # Member-scope (#278): a private loan is only visible to its
        # owner, so act as Josh to create and list it.
        hj = {**h, "X-Profile-Id": str(pid)}
        mine = client.post("/api/v1/loans", headers=hj, json={
            "party": "Mum", "direction": "we_owe",
            "principal": 500000, "start_date": "2025-01-01",
            "owner_profile_id": pid})
        assert mine.status_code == 201, mine.json()
        assert mine.json()["owner_profile_id"] == pid
        shared = client.post("/api/v1/loans", headers=h, json={
            "party": "Dad", "direction": "they_owe",
            "principal": 10000, "start_date": "2025-01-01"})
        items = client.get("/api/v1/loans", headers=hj).json()["items"]
        assert {i["owner_profile_id"] for i in items} == {pid, None}
        assert shared.json()["owner_profile_id"] is None
        # Anonymous (pre-auth compat) sees shared rows only.
        anon = client.get("/api/v1/loans", headers=h).json()["items"]
        assert {i["party"] for i in anon} == {"Dad"}

    def test_projection_endpoint_and_running_balance(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        ln = client.post("/api/v1/loans", headers=h, json={
            "party": "Bank", "direction": "we_owe",
            "principal": 120_000, "start_date": "2025-01-01",
            "expected_payment": 10_000})
        lid = ln.json()["id"]
        for i in range(2):
            client.post(f"/api/v1/loans/{lid}/repayments", headers=h,
                        json={"date": f"2025-02-0{i + 1}",
                              "amount": 10_000})
        hist = client.get(f"/api/v1/loans/{lid}/repayments",
                          headers=h).json()
        assert hist[0]["balance_after_pence"] == 110_000
        assert hist[1]["balance_after_pence"] == 100_000
        proj = client.get(
            f"/api/v1/loans/{lid}/projection?extra_monthly=5000",
            headers=h).json()
        assert proj["baseline"]["months"] is not None
        assert proj["with_extra"]["months"] < proj["baseline"]["months"]
        assert proj["months_saved"] > 0
        assert proj["extra_monthly_pence"] == 5_000


class TestPatchApi:
    """Issue #278 — loans are editable (PATCH) with member-scope RBAC:
    shared/own-private writable, other-private invisible (404) and
    immutable (403)."""

    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def _setup(self, client: TestClient):
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        admin = client.post("/api/v1/profiles", headers=h, json={
            "display_name": "Admin", "role": "admin"}).json()
        member = client.post("/api/v1/profiles", headers=h, json={
            "display_name": "Member", "role": "member"}).json()
        ha = {**h, "X-Profile-Id": str(admin["id"])}
        hm = {**h, "X-Profile-Id": str(member["id"])}
        return h, ha, hm, admin, member

    def test_patch_updates_terms(self, client: TestClient) -> None:
        h = {"X-Household-Id": str(client.post(
            "/api/v1/households", json={"name": "H"}).json()["id"])}
        ln = client.post("/api/v1/loans", headers=h, json={
            "party": "Mum", "direction": "we_owe",
            "principal": 500000, "start_date": "2025-01-01",
            "expected_payment": 10000})
        assert ln.status_code == 201, ln.json()
        lid = ln.json()["id"]
        r = client.patch(f"/api/v1/loans/{lid}", headers=h, json={
            "party": "Mum (updated)", "principal_pence": 450000,
            "start_date": "2024-06-01", "direction": "they_owe",
            "expected_payment_pence": 8000, "interest_bps_pa": 500,
            "notes": "corrected"})
        assert r.status_code == 200, r.json()
        out = r.json()
        assert out["party"] == "Mum (updated)"
        assert out["principal_pence"] == 450_000
        assert out["start_date"] == "2024-06-01"
        assert out["direction"] == "they_owe"
        assert out["expected_payment_pence"] == 8_000
        assert out["interest_bps_pa"] == 500
        assert out["notes"] == "corrected"
        assert out["active"] is True
        # settle + reactivate through the same endpoint
        assert client.patch(f"/api/v1/loans/{lid}", headers=h,
                            json={"active": False}).json()["active"] is False
        assert client.patch(f"/api/v1/loans/{lid}", headers=h,
                            json={"active": True}).json()["active"] is True

    def test_member_scope_gates(self, client: TestClient) -> None:
        h, ha, hm, admin, member = self._setup(client)
        # Admin-owned private loan — invisible to the member.
        mine = client.post("/api/v1/loans", headers=ha, json={
            "party": "Admin loan", "direction": "they_owe",
            "principal": 10000, "start_date": "2025-01-01",
            "owner_profile_id": admin["id"]}).json()
        lid = mine["id"]
        assert client.get(f"/api/v1/loans/{lid}",
                          headers=hm).status_code == 404
        assert client.patch(f"/api/v1/loans/{lid}", headers=hm,
                            json={"party": "x"}).status_code == 404
        # Member-owned private loan — member edits own scope.
        own = client.post("/api/v1/loans", headers=hm, json={
            "party": "Member loan", "direction": "they_owe",
            "principal": 10000, "start_date": "2025-01-01",
            "owner_profile_id": member["id"]}).json()
        mid = own["id"]
        r = client.patch(f"/api/v1/loans/{mid}", headers=hm,
                         json={"notes": "mine"})
        assert r.status_code == 200
        # Member may toggle own↔shared, never to another member.
        assert client.patch(f"/api/v1/loans/{mid}", headers=hm, json={
            "owner_profile_id": admin["id"]}).status_code == 403
        assert client.patch(f"/api/v1/loans/{mid}", headers=hm, json={
            "owner_profile_id": None}).status_code == 200
        # Admin edits anything.
        assert client.patch(f"/api/v1/loans/{lid}", headers=ha,
                            json={"notes": "by admin"}).status_code == 200

    def test_list_and_write_visibility(self, client: TestClient) -> None:
        h, ha, hm, admin, member = self._setup(client)
        client.post("/api/v1/loans", headers=ha, json={
            "party": "Admin only", "direction": "we_owe",
            "principal": 10000, "start_date": "2025-01-01",
            "owner_profile_id": admin["id"]})
        client.post("/api/v1/loans", headers=hm, json={
            "party": "Member only", "direction": "we_owe",
            "principal": 20000, "start_date": "2025-01-01",
            "owner_profile_id": member["id"]})
        client.post("/api/v1/loans", headers=ha, json={
            "party": "Shared", "direction": "we_owe",
            "principal": 30000, "start_date": "2025-01-01"})
        parties = {i["party"] for i in
                   client.get("/api/v1/loans", headers=hm).json()["items"]}
        assert parties == {"Member only", "Shared"}
        # Member cannot record repayments on admin's private loan
        # (the loan is not even visible, so the sub-resource 404s).
        admin_loan = client.post("/api/v1/loans", headers=ha, json={
            "party": "A2", "direction": "we_owe",
            "principal": 5000, "start_date": "2025-01-01",
            "owner_profile_id": admin["id"]}).json()
        assert client.post(
            f"/api/v1/loans/{admin_loan['id']}/repayments",
            headers=hm, json={"date": "2025-02-01",
                              "amount": 100}).status_code == 404


class TestProjection:
    """Issue #94 — deterministic debt-free projection."""

    def _loan(self, session: Session, household: Household,
              principal: int = 120_000, expected: int = 10_000,
              interest_bps: int = 0) -> Loan:
        ln = Loan(household_id=household.id, party="Bank",
                  direction=LoanDirection.we_owe,
                  principal_pence=principal,
                  start_date=date(2025, 1, 1),
                  expected_payment_pence=expected,
                  interest_bps_pa=interest_bps, active=True)
        session.add(ln)
        session.flush()
        return ln

    def test_golden_no_interest(
            self, session: Session, household: Household) -> None:
        ln = self._loan(session, household)
        p = loans.projected_payoff(
            session, ln, on=date(2025, 1, 15))
        # £120,000 at £10,000/mo, no interest → 12 months → Jan 2026
        assert p.months == 12
        assert p.payoff_date == date(2026, 1, 1)

    def test_extra_moves_date_deterministically(
            self, session: Session, household: Household) -> None:
        ln = self._loan(session, household)
        on = date(2025, 1, 15)
        base = loans.projected_payoff(session, ln, on=on)
        extra = loans.projected_payoff(
            session, ln, extra_monthly_pence=10_000, on=on)
        # doubling the rate halves the term
        assert extra.months == 6
        assert extra.payoff_date == date(2025, 7, 1)
        assert base.months - extra.months == 6

    def test_after_repayments_remaining_shortens(
            self, session: Session, household: Household,
            loan: Loan) -> None:
        session.add(Repayment(loan_id=loan.id,
                              date=date(2025, 2, 1),
                              amount_pence=200_000))
        session.flush()
        p = loans.projected_payoff(
            session, loan, on=date(2025, 2, 10))
        # £500k − £200k repaid = £300k at £10k/mo → 30 months
        assert p.months == 30

    def test_no_rate_never_pays_off(
            self, session: Session, household: Household) -> None:
        ln = Loan(household_id=household.id, party="Free",
                  direction=LoanDirection.we_owe,
                  principal_pence=50_000,
                  start_date=date(2025, 1, 1), active=True)
        session.add(ln)
        session.flush()
        p = loans.projected_payoff(
            session, ln, on=date(2025, 1, 15))
        assert p.months is None and p.payoff_date is None

    def test_interest_extends_beyond_simple_division(
            self, session: Session, household: Household) -> None:
        ln = self._loan(session, household,
                        principal=100_000, expected=10_000,
                        interest_bps=1_200)  # 12% pa
        p = loans.projected_payoff(
            session, ln, on=date(2025, 1, 15))
        # naive division says 10 months; ~1% per month of accrued
        # interest eats a month — pays off at 12
        assert p.months == 12

    def test_running_balance_history(
            self, session: Session, household: Household,
            loan: Loan) -> None:
        session.add(Repayment(loan_id=loan.id,
                              date=date(2025, 1, 15),
                              amount_pence=10_000))
        session.add(Repayment(loan_id=loan.id,
                              date=date(2025, 2, 15),
                              amount_pence=10_000))
        session.flush()
        rows = loans.repayment_balances(session, loan)
        assert [b for _, b in rows] == [490_000, 480_000]
