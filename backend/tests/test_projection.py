"""Issue #74 — daily projection, safe-to-spend, min-balance warning.

Golden AC: balance £1,000, £650 committed before payday, £200 after
→ safe-to-spend = £350 exactly.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import projection
from app.main import create_app
from app.persistence.models import (
    Account,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Household,
    Transaction,
    TransactionStatus,
)

TODAY = date(2025, 4, 10)


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Current",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    session.add(Transaction(
        account_id=a.id, date=date(2025, 4, 1), amount_pence=100_000,
        payee="seed", status=TransactionStatus.cleared,
        source="manual"))
    session.flush()
    return a


def _expect(session: Session, household: Household, a: Account,
            on: date, pence: int,
            source: ExpectedSource = ExpectedSource.commitment,
            label: str = "x") -> None:
    session.add(ExpectedMovement(
        household_id=household.id, account_id=a.id, expected_date=on,
        amount_pence=pence, label=label,
        confidence=Confidence.scheduled, source_type=source,
        source_id=1))
    session.flush()


class TestSafeToSpend:
    def test_golden_650_before_payday(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC golden: £1,000 − £650 committed ≤ payday = £350; the
        £200 due after payday does not count."""
        payday = date(2025, 4, 25)
        _expect(session, household, account, date(2025, 4, 15),
                -40_000, label="rent")
        _expect(session, household, account, date(2025, 4, 20),
                -25_000, label="bills")
        _expect(session, household, account, payday, 200_000,
                source=ExpectedSource.payroll_run, label="wages")
        _expect(session, household, account, date(2025, 4, 26),
                -20_000, label="next month's treat")

        p = projection.project(session, account, 30, from_date=TODAY)
        assert p.next_payday == payday
        assert p.committed_until_payday_pence == 65_000
        assert p.safe_to_spend_pence == 35_000

    def test_projected_employment_payday_counts(
            self, session: Session, household: Household,
            account: Account) -> None:
        """Issue #454 — a rule-projected payday (employment source) is
        a real payday for safe-to-spend and the income forecast."""
        payday = date(2025, 4, 25)
        _expect(session, household, account, date(2025, 4, 15),
                -40_000, label="rent")
        _expect(session, household, account, payday, 200_000,
                source=ExpectedSource.employment, label="wages")

        p = projection.project(session, account, 30, from_date=TODAY)
        assert p.next_payday == payday
        assert p.committed_until_payday_pence == 40_000
        # The income row itself lands in the daily series.
        payday_pt = next(d for d in p.days if d.date == payday)
        assert payday_pt.movements_pence > 0

    def test_obligation_on_payday_counts(
            self, session: Session, household: Household,
            account: Account) -> None:
        """Boundary: money due ON payday is still committed — wages
        may not have cleared yet."""
        payday = date(2025, 4, 25)
        _expect(session, household, account, payday, -30_000,
                label="due on payday")
        _expect(session, household, account, payday, 200_000,
                source=ExpectedSource.payroll_run, label="wages")
        p = projection.project(session, account, 30, from_date=TODAY)
        assert p.committed_until_payday_pence == 30_000
        assert p.safe_to_spend_pence == 70_000

    def test_no_payroll_whole_horizon_commits(
            self, session: Session, household: Household,
            account: Account) -> None:
        _expect(session, household, account, date(2025, 4, 15), -5_000)
        _expect(session, household, account, date(2025, 5, 9), -7_000)
        p = projection.project(session, account, 30, from_date=TODAY)
        assert p.next_payday is None
        assert p.committed_until_payday_pence == 12_000
        assert p.safe_to_spend_pence == 88_000

    def test_income_before_payday_not_obligation(
            self, session: Session, household: Household,
            account: Account) -> None:
        _expect(session, household, account, date(2025, 4, 15), 8_000,
                label="rebate")
        _expect(session, household, account, date(2025, 4, 25),
                200_000, source=ExpectedSource.payroll_run)
        p = projection.project(session, account, 30, from_date=TODAY)
        assert p.committed_until_payday_pence == 0
        assert p.safe_to_spend_pence == 100_000


class TestDailySeries:
    def test_deterministic_and_cumulative(
            self, session: Session, household: Household,
            account: Account) -> None:
        _expect(session, household, account, date(2025, 4, 11), -5_000)
        _expect(session, household, account, date(2025, 4, 12), -3_000)
        p1 = projection.project(session, account, 5, from_date=TODAY)
        p2 = projection.project(session, account, 5, from_date=TODAY)
        assert [d.balance_pence for d in p1.days] == \
            [d.balance_pence for d in p2.days]
        assert [d.balance_pence for d in p1.days[:3]] == \
            [95_000, 92_000, 92_000]
        assert p1.min_balance_pence == 92_000
        assert p1.min_balance_date == date(2025, 4, 12)

    def test_min_balance_date_is_first_lowest(
            self, session: Session, household: Household,
            account: Account) -> None:
        _expect(session, household, account, date(2025, 4, 12), -90_000)
        _expect(session, household, account, date(2025, 4, 20), 5_000)
        p = projection.project(session, account, 30, from_date=TODAY)
        assert p.min_balance_pence == 10_000
        assert p.min_balance_date == date(2025, 4, 12)
        assert p.below_threshold is False


class TestThreshold:
    def test_below_threshold_warns(
            self, session: Session, household: Household,
            account: Account) -> None:
        account.min_balance_pence = 50_000
        session.flush()
        _expect(session, household, account, date(2025, 4, 12), -60_000)
        p = projection.project(session, account, 30, from_date=TODAY)
        assert p.threshold_pence == 50_000
        assert p.min_balance_pence == 40_000
        assert p.below_threshold is True

    def test_no_threshold_never_warns(
            self, session: Session, household: Household,
            account: Account) -> None:
        _expect(session, household, account, date(2025, 4, 12), -60_000)
        p = projection.project(session, account, 30, from_date=TODAY)
        assert p.threshold_pence is None
        assert p.below_threshold is False


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def _seed(self, client: TestClient) -> tuple[dict, int]:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", json={
            "name": "Current", "type": "personal",
            "opening_balance": 100_000,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        return h, aid

    def test_projection_endpoint(self, client: TestClient) -> None:
        h, aid = self._seed(client)
        future = (date.today() + timedelta(days=5)).isoformat()
        client.post("/api/v1/expected-movements", json={
            "expected_date": future, "amount": -10_000,
            "account_id": aid, "label": "Bill"}, headers=h)
        res = client.get(
            f"/api/v1/accounts/{aid}/projection", params={"days": 20})
        assert res.status_code == 200
        body = res.json()
        assert body["account_id"] == aid
        assert body["start_balance_pence"] == 100_000
        assert len(body["days"]) == 20
        assert any(d["movements_pence"] == -10_000
                   for d in body["days"])

    def test_min_balance_patchable(self, client: TestClient) -> None:
        h, aid = self._seed(client)
        res = client.patch(f"/api/v1/accounts/{aid}",
                           json={"min_balance_pence": 25_000},
                           headers=h)
        assert res.status_code == 200
        assert res.json()["min_balance_pence"] == 25_000
        got = client.get(f"/api/v1/accounts/{aid}", headers=h).json()
        assert got["min_balance_pence"] == 25_000
