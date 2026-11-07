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
