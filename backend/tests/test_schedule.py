"""Issue #72 — expected-movement schedule: traceability,
idempotent re-materialisation, future-only regeneration."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import pay_calendar, paydays, payrun, schedule
from app.domain import payroll as dom
from app.main import create_app
from app.persistence.models import (
    Account,
    Confidence,
    Employment,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    RecurringCommitment,
    UserProfile,
)

TODAY = date(2025, 4, 10)


@pytest.fixture()
def commitment(session: Session, household: Household
               ) -> RecurringCommitment:
    a = Account(household_id=household.id, name="Joint",
                type="joint", opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    c = RecurringCommitment(
        household_id=household.id, name="Rent", account_id=a.id,
        amount_pence=-150_000, frequency=Frequency.monthly,
        day_of_month=1, next_date=date(2025, 4, 1))
    session.add(c)
    session.flush()
    return c


def _rows(session: Session) -> list[ExpectedMovement]:
    return list(session.execute(select(ExpectedMovement)).scalars())


class TestMaterialise:
    def test_commitment_traceable(self, session: Session,
                                  commitment: RecurringCommitment
                                  ) -> None:
        """AC: every movement is traceable to its source."""
        n = schedule.rematerialize_commitment(
            session, commitment, today=TODAY)
        assert n > 0
        for m in _rows(session):
            assert m.source_type is ExpectedSource.commitment
            assert m.source_id == commitment.id
            assert m.amount_pence == -150_000
            assert m.confidence is Confidence.scheduled
            assert m.expected_date >= TODAY
            assert m.expected_date.day == 1

    def test_idempotent(self, session: Session,
                        commitment: RecurringCommitment) -> None:
        n1 = schedule.rematerialize_commitment(
            session, commitment, today=TODAY)
        dates1 = {m.expected_date for m in _rows(session)}
        n2 = schedule.rematerialize_commitment(
            session, commitment, today=TODAY)
        assert n1 == n2
        rows = _rows(session)
        assert len(rows) == n1
        assert {m.expected_date for m in rows} == dates1

    def test_future_only(self, session: Session,
                         commitment: RecurringCommitment) -> None:
        """AC: editing a commitment re-materialises FUTURE items
        only — a past row stays untouched."""
        schedule.rematerialize_commitment(session, commitment,
                                          today=date(2025, 3, 15))
        past = [m for m in _rows(session)
                if m.expected_date < TODAY]
        assert past and past[0].expected_date == date(2025, 4, 1)
        commitment.amount_pence = -160_000
        schedule.rematerialize_commitment(session, commitment,
                                          today=TODAY)
        rows = _rows(session)
        still_past = [m for m in rows if m.expected_date < TODAY]
        assert [m.id for m in still_past] == [m.id for m in past]
        assert still_past[0].amount_pence == -150_000   # history kept
        assert all(m.amount_pence == -160_000
                   for m in rows if m.expected_date >= TODAY)

    def test_inactive_drops_future(self, session: Session,
                                   commitment: RecurringCommitment
                                   ) -> None:
        schedule.rematerialize_commitment(session, commitment,
                                          today=TODAY)
        assert _rows(session)
        commitment.active = False
        n = schedule.rematerialize_commitment(session, commitment,
                                              today=TODAY)
        assert n == 0 and _rows(session) == []


class TestPayrollSource:
    def test_forecast_estimated_then_scheduled(
            self, session: Session, household: Household) -> None:
        p = UserProfile(household_id=household.id, display_name="J")
        session.add(p)
        session.flush()
        e = Employment(household_id=household.id, profile_id=p.id,
                       employer="Acme", jurisdiction="england_wales",
                       contracted_hours=Decimal("37.5"))
        session.add(e)
        session.flush()
        dom.add_salary_revision(session, e, date(2025, 1, 1),
                                3_600_000)
        dom.add_tax_code_revision(session, e, date(2025, 1, 1),
                                  "1257L")
        run = payrun.generate(session, e, start=date(2025, 4, 1),
                              end=date(2025, 4, 30),
                              pay_date=TODAY + timedelta(days=10))
        schedule.rematerialize_payroll_run(session, run, today=TODAY)
        (m,) = _rows(session)
        assert m.source_type is ExpectedSource.payroll_run
        assert m.confidence is Confidence.estimated
        assert m.amount_pence == run.net_pence
        payrun.reconcile(session, run, {})
        schedule.rematerialize_payroll_run(session, run, today=TODAY)
        (m,) = _rows(session)
        assert m.confidence is Confidence.scheduled

    def test_corrected_emits_nothing(self, session: Session,
                                     household: Household) -> None:
        p = UserProfile(household_id=household.id, display_name="J")
        session.add(p)
        session.flush()
        e = Employment(household_id=household.id, profile_id=p.id,
                       employer="Acme", jurisdiction="england_wales",
                       contracted_hours=Decimal("37.5"))
        session.add(e)
        session.flush()
        dom.add_salary_revision(session, e, date(2025, 1, 1),
                                3_600_000)
        dom.add_tax_code_revision(session, e, date(2025, 1, 1),
                                  "1257L")
        run = payrun.generate(session, e, start=date(2025, 4, 1),
                              end=date(2025, 4, 30),
                              pay_date=TODAY + timedelta(days=10))
        payrun.reconcile(session, run, {})
        payrun.correct(session, run)
        schedule.rematerialize_payroll_run(session, run, today=TODAY)
        assert _rows(session) == []


class TestEmploymentProjection:
    """Issue #454 — projected paydays from the payday rule so salary
    reaches the forecast before a payroll run exists."""

    RULE = pay_calendar.serialize_rule(paydays.PaydayRule(
        kind=paydays.RuleKind.fixed_day, day=28,
        adjust=paydays.ShiftPolicy.previous))

    def _employment(self, session: Session, household: Household,
                    account: Account | None) -> Employment:
        p = UserProfile(household_id=household.id, display_name="J")
        session.add(p)
        session.flush()
        e = Employment(
            household_id=household.id, profile_id=p.id,
            employer="Acme", jurisdiction="england_wales",
            contracted_hours=Decimal("37.5"),
            payday_rule=self.RULE,
            paid_into_account_id=account.id if account else None)
        session.add(e)
        session.flush()
        dom.add_salary_revision(session, e, date(2025, 1, 1), 3_600_000)
        dom.add_tax_code_revision(session, e, date(2025, 1, 1), "1257L")
        return e

    def _account(self, session: Session, household: Household) -> Account:
        a = Account(household_id=household.id, name="Joint",
                    type="joint", opening_date=date(2025, 1, 1))
        session.add(a)
        session.flush()
        return a

    def _employment_rows(self, session: Session) -> list[ExpectedMovement]:
        return list(session.scalars(
            select(ExpectedMovement).where(
                ExpectedMovement.source_type
                == ExpectedSource.employment)).all())

    def test_projects_future_paydays(
            self, session: Session, household: Household) -> None:
        a = self._account(session, household)
        e = self._employment(session, household, a)
        payrun.generate(
            session, e, start=date(2025, 3, 1), end=date(2025, 3, 31),
            pay_date=date(2025, 3, 28))   # historical pay must not set future net
        n = schedule.rematerialize_employment(
            session, e, today=TODAY, horizon_days=60)
        rows = self._employment_rows(session)
        assert n == len(rows) and n >= 2
        assert all(m.account_id == a.id for m in rows)
        assert all(m.amount_pence == 245330 for m in rows)
        assert all(m.confidence is Confidence.estimated for m in rows)
        # First projected payday after 2025-04-10 on day-28 rule.
        assert rows[0].expected_date == date(2025, 4, 28)

    def test_no_account_emits_nothing(
            self, session: Session, household: Household) -> None:
        e = self._employment(session, household, None)
        n = schedule.rematerialize_employment(session, e, today=TODAY)
        assert n == 0 and self._employment_rows(session) == []

    def test_run_covered_payday_skipped(
            self, session: Session, household: Household) -> None:
        a = self._account(session, household)
        e = self._employment(session, household, a)
        run = payrun.generate(
            session, e, start=date(2025, 3, 1), end=date(2025, 3, 31),
            pay_date=date(2025, 3, 28))
        schedule.rematerialize_payroll_run(session, run, today=TODAY)
        future = payrun.generate(
            session, e, start=date(2025, 4, 1), end=date(2025, 4, 30),
            pay_date=date(2025, 4, 28))
        schedule.rematerialize_payroll_run(
            session, future, today=TODAY)
        schedule.rematerialize_employment(
            session, e, today=TODAY, horizon_days=60)
        dates = {m.expected_date for m in self._employment_rows(session)}
        assert date(2025, 4, 28) not in dates   # the real run owns it
        assert date(2025, 5, 28) in dates

    def test_inactive_emits_nothing(
            self, session: Session, household: Household) -> None:
        a = self._account(session, household)
        e = self._employment(session, household, a)
        payrun.generate(
            session, e, start=date(2025, 3, 1), end=date(2025, 3, 31),
            pay_date=date(2025, 3, 28))
        e.is_active = False
        session.flush()
        n = schedule.rematerialize_employment(session, e, today=TODAY)
        assert n == 0 and self._employment_rows(session) == []

    def test_estimate_without_runs_uses_tax_engine(
            self, session: Session, household: Household) -> None:
        a = self._account(session, household)
        e = self._employment(session, household, a)
        n = schedule.rematerialize_employment(
            session, e, today=TODAY, horizon_days=35)
        rows = self._employment_rows(session)
        assert n == len(rows) == 1
        # £36,000 gross → sensible net band, never the gross figure.
        assert 150_000 < rows[0].amount_pence < 300_000


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_manual_item_flow(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        r = client.post("/api/v1/expected-movements", json={
            "expected_date": "2025-06-01", "amount": 50000,
            "label": "Birthday money"}, headers=h)
        assert r.status_code == 201
        mid = r.json()["id"]
        assert r.json()["source_type"] == "manual"
        assert r.json()["source_id"] == mid  # self-referential
        rows = client.get("/api/v1/expected-movements"
                          "?start=2025-05-01&end=2025-07-01",
                          headers=h).json()
        assert [m["id"] for m in rows] == [mid]
        assert client.delete(f"/api/v1/expected-movements/{mid}",
                             headers=h).status_code == 204

    def test_commitment_create_materialises(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", json={
            "name": "Joint", "type": "joint", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        future = (date.today() + timedelta(days=30)).isoformat()
        r = client.post("/api/v1/commitments", json={
            "name": "Rent", "account_id": aid, "amount": -150000,
            "frequency": "monthly", "next_date": future},
            headers=h)
        assert r.status_code == 201
        rows = client.get("/api/v1/expected-movements"
                          "?start=2025-01-01&end=2027-01-01",
                          headers=h).json()
        assert len(rows) >= 1
        assert all(m["source_type"] == "commitment" for m in rows)
        assert all(m["label"] == "Rent" for m in rows)
