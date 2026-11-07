"""Issue #76 — what-if scenario layer.

Golden AC: baseline + an added expense on a chosen date shifts the
minimum-balance day correctly; real data provably unmodified after
scenario play.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import scenario as scen
from app.domain.scenario import ScenarioError
from app.main import create_app
from app.persistence.models import (
    Account,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Household,
    Scenario,
    ScenarioOpKind,
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
            label: str = "x") -> ExpectedMovement:
    m = ExpectedMovement(
        household_id=household.id, account_id=a.id, expected_date=on,
        amount_pence=pence, label=label,
        confidence=Confidence.scheduled, source_type=source,
        source_id=1)
    session.add(m)
    session.flush()
    return m


def _scenario(session: Session, household: Household,
              name: str = "What-if") -> Scenario:
    return scen.create_scenario(session, household.id, name)


class TestAddOp:
    def test_golden_added_expense_shifts_min_day(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: baseline min on day 5 @ £600; +£700 expense on day 8
        pulls the trough to day 8 @ −£100."""
        _expect(session, household, account, TODAY + timedelta(days=5),
                -40_000)
        s = _scenario(session, household)
        scen.add_op(session, s, kind=ScenarioOpKind.add,
                    account_id=account.id,
                    expected_date=TODAY + timedelta(days=8),
                    amount_pence=-70_000, label="Big purchase")
        r = scen.run(session, s, account, days=15, from_date=TODAY)
        assert r.baseline.min_balance_date == TODAY + timedelta(days=5)
        assert r.baseline.min_balance_pence == 60_000
        assert r.projection.min_balance_date == TODAY + timedelta(days=8)
        assert r.projection.min_balance_pence == -10_000
        assert r.diff.min_balance_changed is True
        assert r.diff.min_balance_delta_pence == -70_000

    def test_add_op_requires_payload(
            self, session: Session, household: Household) -> None:
        s = _scenario(session, household)
        with pytest.raises(ScenarioError, match="account_id"):
            scen.add_op(session, s, kind=ScenarioOpKind.add,
                        label="oops", expected_date=TODAY,
                        amount_pence=-1)

    def test_add_other_account_noop(
            self, session: Session, household: Household,
            account: Account) -> None:
        other = Account(household_id=household.id, name="Savings",
                        type="personal", opening_balance_pence=0,
                        opening_date=date(2025, 1, 1))
        session.add(other)
        session.flush()
        s = _scenario(session, household)
        scen.add_op(session, s, kind=ScenarioOpKind.add,
                    account_id=other.id,
                    expected_date=TODAY + timedelta(days=3),
                    amount_pence=-50_000, label="not mine")
        r = scen.run(session, s, account, days=10, from_date=TODAY)
        assert r.projection.min_balance_pence == 100_000


class TestRemoveOp:
    def test_remove_lifts_trough(
            self, session: Session, household: Household,
            account: Account) -> None:
        bill = _expect(session, household, account,
                       TODAY + timedelta(days=5), -40_000)
        s = _scenario(session, household)
        scen.add_op(session, s, kind=ScenarioOpKind.remove,
                    expected_movement_id=bill.id)
        r = scen.run(session, s, account, days=10, from_date=TODAY)
        assert r.baseline.min_balance_pence == 60_000
        assert r.projection.min_balance_pence == 100_000
        assert r.diff.min_balance_delta_pence == 40_000

    def test_remove_missing_movement_rejected(
            self, session: Session, household: Household) -> None:
        s = _scenario(session, household)
        with pytest.raises(ScenarioError, match="not found"):
            scen.add_op(session, s, kind=ScenarioOpKind.remove,
                        expected_movement_id=999999)


class TestShiftOp:
    def test_shift_moves_trough_day(
            self, session: Session, household: Household,
            account: Account) -> None:
        bill = _expect(session, household, account,
                       TODAY + timedelta(days=5), -40_000)
        s = _scenario(session, household)
        scen.add_op(session, s, kind=ScenarioOpKind.shift,
                    expected_movement_id=bill.id,
                    expected_date=TODAY + timedelta(days=12))
        r = scen.run(session, s, account, days=15, from_date=TODAY)
        assert r.baseline.min_balance_date == TODAY + timedelta(days=5)
        assert r.projection.min_balance_date == TODAY + timedelta(days=12)
        assert r.projection.min_balance_pence == 60_000

    def test_shift_out_of_window(
            self, session: Session, household: Household,
            account: Account) -> None:
        bill = _expect(session, household, account,
                       TODAY + timedelta(days=5), -40_000)
        s = _scenario(session, household)
        scen.add_op(session, s, kind=ScenarioOpKind.shift,
                    expected_movement_id=bill.id,
                    expected_date=TODAY + timedelta(days=60))
        r = scen.run(session, s, account, days=15, from_date=TODAY)
        assert r.projection.min_balance_pence == 100_000

    def test_shift_requires_new_date(
            self, session: Session, household: Household,
            account: Account) -> None:
        bill = _expect(session, household, account,
                       TODAY + timedelta(days=5), -1_000)
        s = _scenario(session, household)
        with pytest.raises(ScenarioError, match="expected_date"):
            scen.add_op(session, s, kind=ScenarioOpKind.shift,
                        expected_movement_id=bill.id)


class TestPaydayDelta:
    def test_payday_balances_diff(
            self, session: Session, household: Household,
            account: Account) -> None:
        payday = TODAY + timedelta(days=10)
        _expect(session, household, account, payday, 200_000,
                source=ExpectedSource.payroll_run, label="wages")
        _expect(session, household, account,
                TODAY + timedelta(days=4), -30_000)
        s = _scenario(session, household)
        scen.add_op(session, s, kind=ScenarioOpKind.add,
                    account_id=account.id,
                    expected_date=TODAY + timedelta(days=6),
                    amount_pence=-25_000, label="holiday")
        r = scen.run(session, s, account, days=15, from_date=TODAY)
        assert len(r.diff.paydays) == 1
        p = r.diff.paydays[0]
        assert p.date == payday
        assert p.baseline_balance_pence == 270_000
        assert p.scenario_balance_pence == 245_000
        assert p.delta_pence == -25_000
        # Committed obligations until payday rose by the added bill.
        assert r.diff.safe_to_spend_delta_pence == -25_000


class TestIsolation:
    def test_real_stream_unmodified(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: playing a scenario must not touch real data."""
        _expect(session, household, account,
                TODAY + timedelta(days=5), -40_000)
        s = _scenario(session, household)
        before = session.execute(
            select(ExpectedMovement.id,
                   ExpectedMovement.expected_date,
                   ExpectedMovement.amount_pence)
            .order_by(ExpectedMovement.id)).all()
        scen.add_op(session, s, kind=ScenarioOpKind.add,
                    account_id=account.id,
                    expected_date=TODAY + timedelta(days=3),
                    amount_pence=-999_000, label="phantom")
        scen.run(session, s, account, days=10, from_date=TODAY)
        after = session.execute(
            select(ExpectedMovement.id,
                   ExpectedMovement.expected_date,
                   ExpectedMovement.amount_pence)
            .order_by(ExpectedMovement.id)).all()
        assert before == after

    def test_already_negative_baseline(
            self, session: Session, household: Household,
            account: Account) -> None:
        """Edge case: scenario on an already-negative projection."""
        _expect(session, household, account,
                TODAY + timedelta(days=2), -150_000)
        s = _scenario(session, household)
        scen.add_op(session, s, kind=ScenarioOpKind.add,
                    account_id=account.id,
                    expected_date=TODAY + timedelta(days=4),
                    amount_pence=-20_000, label="worse")
        r = scen.run(session, s, account, days=10, from_date=TODAY)
        assert r.baseline.min_balance_pence == -50_000
        assert r.projection.min_balance_pence == -70_000
        assert r.diff.min_balance_changed is True

    def test_deterministic_repeat_run(
            self, session: Session, household: Household,
            account: Account) -> None:
        _expect(session, household, account,
                TODAY + timedelta(days=5), -40_000)
        s = _scenario(session, household)
        scen.add_op(session, s, kind=ScenarioOpKind.add,
                    account_id=account.id,
                    expected_date=TODAY + timedelta(days=8),
                    amount_pence=-10_000, label="x")
        r1 = scen.run(session, s, account, days=15, from_date=TODAY)
        r2 = scen.run(session, s, account, days=15, from_date=TODAY)
        assert (r1.diff.scenario_min_balance_pence
                == r2.diff.scenario_min_balance_pence)
        assert (r1.projection.days == r2.projection.days)


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_scenario_lifecycle(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", json={
            "name": "Current", "type": "personal",
            "opening_balance": 100_000,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        sid = client.post("/api/v1/scenarios",
                          json={"name": "Car?"},
                          headers=h).json()["id"]
        op = client.post(f"/api/v1/scenarios/{sid}/ops", json={
            "kind": "add", "account_id": aid,
            "expected_date": (date.today()
                              + timedelta(days=4)).isoformat(),
            "amount": -50_000, "label": "deposit"}, headers=h)
        assert op.status_code == 201
        res = client.get(f"/api/v1/scenarios/{sid}/run",
                         params={"account_id": aid, "days": 10},
                         headers=h)
        assert res.status_code == 200
        body = res.json()
        assert body["scenario_id"] == sid
        assert body["projection"]["min_balance_pence"] == 50_000
        assert body["baseline"]["min_balance_pence"] == 100_000
        assert body["diff"]["min_balance_delta_pence"] == -50_000
        assert client.delete(f"/api/v1/scenarios/{sid}",
                             headers=h).status_code == 204
        assert client.get(f"/api/v1/scenarios/{sid}/run",
                          params={"account_id": aid},
                          headers=h).status_code == 404

    def test_add_op_validation_422(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        sid = client.post("/api/v1/scenarios",
                          json={"name": "S"}, headers=h).json()["id"]
        res = client.post(f"/api/v1/scenarios/{sid}/ops", json={
            "kind": "remove", "expected_movement_id": 424242},
            headers=h)
        assert res.status_code == 422


class TestPersistence:
    def test_scenario_table_rowcount(
            self, session: Session, household: Household,
            account: Account) -> None:
        _expect(session, household, account,
                TODAY + timedelta(days=3), -5_000)
        s = _scenario(session, household, "Saved")
        scen.add_op(session, s, kind=ScenarioOpKind.add,
                    account_id=account.id, expected_date=TODAY,
                    amount_pence=-1, label="x")
        count = session.scalar(
            select(func.count(ExpectedMovement.id)))
        scen.run(session, s, account, days=5, from_date=TODAY)
        assert session.scalar(
            select(func.count(ExpectedMovement.id))) == count
