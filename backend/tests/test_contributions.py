"""Issue #87 — expected transfer legs matched to real movements:
satisfied / partial / overdue states; auto + manual matching."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import contributions
from app.domain.contributions import ContributionState, MatchError
from app.main import create_app
from app.persistence.models import (
    Account,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    Transaction,
    TransactionStatus,
    Transfer,
    TransferRule,
)

TODAY = date(2025, 4, 10)


@pytest.fixture()
def pair(session: Session, household: Household
         ) -> tuple[Account, Account, TransferRule]:
    src = Account(household_id=household.id, name="Joint",
                  type="joint", opening_date=date(2025, 1, 1))
    dst = Account(household_id=household.id, name="Bills",
                  type="bills", opening_date=date(2025, 1, 1))
    session.add_all([src, dst])
    session.flush()
    r = TransferRule(
        household_id=household.id, name="Bills top-up",
        source_account_id=src.id, dest_account_id=dst.id,
        amount_spec='{"kind": "fixed", "pence": 30000}',
        frequency=Frequency.monthly, day_of_month=1,
        start_date=date(2025, 1, 1), active=True)
    session.add(r)
    session.flush()
    return src, dst, r


def _leg(session: Session, household: Household,
         account: Account, pence: int, on: date) -> Transaction:
    t = Transfer(household_id=household.id)
    session.add(t)
    session.flush()
    tx = Transaction(
        account_id=account.id, date=on, amount_pence=pence,
        payee="Move", transfer_id=t.id,
        status=TransactionStatus.cleared, source="manual")
    session.add(tx)
    session.flush()
    return tx


def _movement(session: Session, household: Household,
              account: Account, pence: int, on: date,
              rule_id: int) -> ExpectedMovement:
    m = ExpectedMovement(
        household_id=household.id, account_id=account.id,
        expected_date=on, amount_pence=pence,
        label="Bills top-up (in)", confidence=Confidence.scheduled,
        source_type=ExpectedSource.transfer_rule, source_id=rule_id)
    session.add(m)
    session.flush()
    return m


class TestStates:
    def test_satisfied_by_full_match(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        """AC: £300 rule due on the 1st; a £300 leg satisfies it."""
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        _leg(session, household, dst, 30_000, date(2025, 4, 2))
        assert contributions.auto_match(
            session, household.id, today=TODAY) == 1
        s = contributions.status(session, mv, TODAY)
        assert s.state is ContributionState.satisfied
        assert s.matched_pence == 30_000

    def test_partial_leaves_remainder(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        """AC: £200 vs £300 → partial with £100 outstanding."""
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        _leg(session, household, dst, 20_000, date(2025, 4, 1))
        contributions.auto_match(session, household.id, today=TODAY)
        s = contributions.status(session, mv, TODAY)
        assert s.state is ContributionState.partial
        assert s.outstanding_pence == 10_000

    def test_overdue_when_due_passed(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        s = contributions.status(session, mv, TODAY)
        assert s.state is ContributionState.overdue

    def test_pending_when_due_ahead(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       TODAY + timedelta(days=5), rule.id)
        s = contributions.status(session, mv, TODAY)
        assert s.state is ContributionState.pending


class TestAutoMatch:
    def test_window_edge(self, session: Session, household: Household,
                         pair: tuple[Account, Account, TransferRule]
                         ) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 8), rule.id)
        inside = _leg(session, household, dst, 30_000,
                      date(2025, 4, 15))  # +7d — inside default window
        outside = _leg(session, household, dst, 30_000,
                       date(2025, 4, 16))  # +8d — outside
        n = contributions.auto_match(session, household.id, today=TODAY)
        assert n == 1
        s = contributions.status(session, mv, TODAY)
        assert s.matched_pence == inside.amount_pence
        assert outside.id != inside.id  # sanity

    def test_wrong_sign_never_matches(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        _leg(session, household, dst, -30_000, date(2025, 4, 1))
        assert contributions.auto_match(
            session, household.id, today=TODAY) == 0
        assert contributions.status(
            session, mv, TODAY).matched_pence == 0

    def test_leg_matches_only_one_movement(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        m1 = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        m2 = _movement(session, household, dst, 30_000,
                       date(2025, 4, 3), rule.id)
        _leg(session, household, dst, 30_000, date(2025, 4, 1))
        contributions.auto_match(session, household.id, today=TODAY)
        assert contributions.status(
            session, m1, TODAY).matched_pence == 30_000
        assert contributions.status(
            session, m2, TODAY).matched_pence == 0

    def test_idempotent(self, session: Session, household: Household,
                        pair: tuple[Account, Account, TransferRule]
                        ) -> None:
        _, dst, rule = pair
        _movement(session, household, dst, 30_000,
                  date(2025, 4, 1), rule.id)
        _leg(session, household, dst, 30_000, date(2025, 4, 1))
        n1 = contributions.auto_match(session, household.id,
                                      today=TODAY)
        n2 = contributions.auto_match(session, household.id,
                                      today=TODAY)
        assert (n1, n2) == (1, 0)


class TestManualConfirm:
    def test_confirm_records_match(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        tx = _leg(session, household, dst, 30_000,
                  date(2025, 5, 20))  # outside auto window
        m = contributions.confirm(session, mv, tx, today=TODAY)
        assert m.manual is True
        assert contributions.status(
            session, mv, TODAY).state is ContributionState.satisfied

    def test_confirm_rejects_wrong_account(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        src, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        tx = _leg(session, household, src, 30_000, date(2025, 4, 1))
        with pytest.raises(MatchError):
            contributions.confirm(session, mv, tx, today=TODAY)

    def test_confirm_rejects_double_use(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        m1 = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        m2 = _movement(session, household, dst, 30_000,
                       date(2025, 4, 2), rule.id)
        tx = _leg(session, household, dst, 30_000, date(2025, 4, 1))
        contributions.confirm(session, m1, tx, today=TODAY)
        with pytest.raises(MatchError, match="already matched"):
            contributions.confirm(session, m2, tx, today=TODAY)


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def test_match_and_status_flow(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        src = client.post("/api/v1/accounts", json={
            "name": "Joint", "type": "joint", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        dst = client.post("/api/v1/accounts", json={
            "name": "Bills", "type": "bills", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        client.post("/api/v1/transfer-rules", headers=h, json={
            "name": "Bills", "source_account_id": src,
            "dest_account_id": dst,
            "amount_spec": {"kind": "fixed", "pence": 30000},
            "frequency": "monthly", "day_of_month": 1,
            "start_date": "2025-01-01"})
        # a real transfer on the next due date (rules only
        # materialise future legs)
        today = date.today()
        y, m = (today.year, today.month + 1) if today.month < 12 \
            else (today.year + 1, 1)
        due = date(y, m, 1).isoformat()
        d = client.post("/api/v1/transactions", headers=h, json={
            "account_id": dst, "date": due,
            "amount": 30000, "payee": "Top up"})
        c = client.post("/api/v1/transactions", headers=h, json={
            "account_id": src, "date": due,
            "amount": -30000, "payee": "Top up"})
        tr = client.post("/api/v1/transfers", headers=h, json={
            "debit_transaction_id": c.json()["id"],
            "credit_transaction_id": d.json()["id"]})
        assert tr.status_code == 201, tr.json()
        n = client.post("/api/v1/expected-movements/match",
                        headers=h).json()["matched"]
        assert n >= 1
        rows = client.get("/api/v1/expected-movements/contributions",
                          headers=h, params={
                              "start": due, "end": due}).json()
        states = {r["state"] for r in rows}
        assert "satisfied" in states


class TestOutstanding:
    """Issue #88 — aging, requirement totals, write-off/reschedule."""

    def test_missed_due_appears_with_age(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        _movement(session, household, dst, 30_000,
                  date(2025, 4, 1), rule.id)
        items = contributions.outstanding(
            session, household.id, today=TODAY)
        assert len(items) == 1
        assert items[0].age_days == 9
        assert items[0].outstanding_pence == 30_000

    def test_satisfied_not_outstanding(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        _movement(session, household, dst, 30_000,
                  date(2025, 4, 1), rule.id)
        _leg(session, household, dst, 30_000, date(2025, 4, 1))
        contributions.auto_match(session, household.id, today=TODAY)
        assert contributions.outstanding(
            session, household.id, today=TODAY) == []

    def test_partial_still_outstanding_with_remainder(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        _movement(session, household, dst, 30_000,
                  date(2025, 4, 1), rule.id)
        _leg(session, household, dst, 20_000, date(2025, 4, 1))
        contributions.auto_match(session, household.id, today=TODAY)
        items = contributions.outstanding(
            session, household.id, today=TODAY)
        assert items[0].outstanding_pence == 10_000

    def test_requirements_reconcile(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        """AC: requirement totals = scheduled − satisfied."""
        _, dst, rule = pair
        _movement(session, household, dst, 30_000,
                  date(2025, 4, 1), rule.id)
        _movement(session, household, dst, 30_000,
                  date(2025, 5, 1), rule.id)
        _leg(session, household, dst, 12_000, date(2025, 4, 2))
        contributions.auto_match(session, household.id, today=TODAY)
        reqs = contributions.requirement_totals(
            session, household.id, start=date(2025, 4, 1),
            end=date(2025, 5, 31), today=TODAY)
        assert len(reqs) == 2
        apr = next(r for r in reqs
                   if r.due_date == date(2025, 4, 1))
        assert apr.scheduled_pence == 30_000
        assert apr.satisfied_pence == 12_000
        assert apr.outstanding_pence == apr.scheduled_pence \
            - apr.satisfied_pence

    def test_write_off_excludes_everywhere(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        from datetime import datetime
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        contributions.write_off(session, mv, when=datetime.now())
        assert contributions.outstanding(
            session, household.id, today=TODAY) == []
        assert contributions.requirement_totals(
            session, household.id, start=date(2025, 4, 1),
            end=date(2025, 4, 30)) == []
        with pytest.raises(MatchError, match="already"):
            contributions.write_off(session, mv, when=datetime.now())

    def test_reschedule_moves_due_date(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        contributions.reschedule(session, mv, date(2025, 4, 15),
                                 today=TODAY)
        assert mv.expected_date == date(2025, 4, 15)
        items = contributions.outstanding(
            session, household.id, today=TODAY)
        assert items[0].age_days == 0 if items else True
        # still past-due at 15th? Apr 15 > today Apr 10 → not overdue
        assert contributions.outstanding(
            session, household.id, today=TODAY) == []

    def test_reschedule_rejects_backward_and_dismissed(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        from datetime import datetime
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 10), rule.id)
        with pytest.raises(MatchError, match="after"):
            contributions.reschedule(session, mv, date(2025, 4, 5),
                                     today=TODAY)
        contributions.write_off(session, mv, when=datetime.now())
        with pytest.raises(MatchError, match="written off"):
            contributions.reschedule(session, mv, date(2025, 5, 1),
                                     today=TODAY)


class TestOutstandingApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def test_write_off_and_requirements_endpoints(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        src = client.post("/api/v1/accounts", json={
            "name": "Joint", "type": "joint", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        dst = client.post("/api/v1/accounts", json={
            "name": "Bills", "type": "bills", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        client.post("/api/v1/transfer-rules", headers=h, json={
            "name": "Bills", "source_account_id": src,
            "dest_account_id": dst,
            "amount_spec": {"kind": "fixed", "pence": 30000},
            "frequency": "monthly", "day_of_month": 1,
            "start_date": "2025-01-01"})
        reqs = client.get("/api/v1/expected-movements/requirements",
                          headers=h, params={
                              "start": date.today().isoformat(),
                              "end": "2030-01-01"}).json()
        assert reqs
        assert all(r["outstanding_pence"] == r["scheduled_pence"]
                   for r in reqs)
        mid = reqs[0]["due_date"]
        # write off the first future movement
        moves = client.get("/api/v1/expected-movements", headers=h,
                           params={"start": mid,
                                   "end": "2030-01-01"}).json()
        in_leg = next(m for m in moves if m["amount_pence"] > 0)
        wo = client.post(
            f"/api/v1/expected-movements/{in_leg['id']}/write-off",
            headers=h)
        assert wo.status_code == 200
        after = client.get(
            "/api/v1/expected-movements/requirements",
            headers=h, params={"start": mid, "end": "2030-01-01"}
        ).json()
        assert len(after) < len(reqs)
