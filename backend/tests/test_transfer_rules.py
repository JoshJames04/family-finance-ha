"""Issue #86 — configurable transfer rules: amount derivation per
kind, expected-movement legs, audited recompute on budget approval."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import contributions, schedule, transfer_rules
from app.domain.transfer_rules import TransferRuleError
from app.main import create_app
from app.persistence.models import (
    Account,
    AmountKind,
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetStatus,
    Category,
    Confidence,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    Transaction,
    TransferRule,
)

TODAY = date(2025, 4, 10)


@pytest.fixture()
def accounts(session: Session, household: Household
             ) -> tuple[Account, Account]:
    src = Account(household_id=household.id, name="Personal",
                  type="personal", opening_date=date(2025, 1, 1))
    dst = Account(household_id=household.id, name="Joint",
                  type="joint", opening_date=date(2025, 1, 1))
    session.add_all([src, dst])
    session.flush()
    return src, dst


def _rule(session: Session, household: Household,
          accounts: tuple[Account, Account],
          spec: dict) -> TransferRule:
    src, dst = accounts
    r = TransferRule(
        household_id=household.id, name="Food money",
        source_account_id=src.id, dest_account_id=dst.id,
        amount_spec=json.dumps(spec), frequency=Frequency.monthly,
        day_of_month=1, start_date=date(2025, 1, 1), active=True)
    session.add(r)
    session.flush()
    return r


def _rows(session: Session, rule: TransferRule) -> list[ExpectedMovement]:
    return list(session.execute(
        select(ExpectedMovement).where(
            ExpectedMovement.source_type == ExpectedSource.transfer_rule,
            ExpectedMovement.source_id == rule.id)).scalars())


class TestParseSpec:
    def test_fixed(self) -> None:
        s = transfer_rules.parse_spec({"kind": "fixed", "pence": 5000})
        assert s.kind is AmountKind.fixed
        assert s.pence == 5000

    def test_fixed_rejects_nonpositive(self) -> None:
        with pytest.raises(TransferRuleError):
            transfer_rules.parse_spec({"kind": "fixed", "pence": 0})

    def test_income_pct_bounds(self) -> None:
        with pytest.raises(TransferRuleError):
            transfer_rules.parse_spec(
                {"kind": "income_pct", "bps": 10001})
        s = transfer_rules.parse_spec({"kind": "income_pct", "bps": 2500})
        assert s.bps == 2500

    def test_budget_line(self) -> None:
        s = transfer_rules.parse_spec(
            {"kind": "budget_line", "category_id": 4})
        assert s.category_id == 4


class TestEvaluate:
    def test_fixed_amount(self, session: Session, household: Household,
                          accounts: tuple[Account, Account]) -> None:
        r = _rule(session, household, accounts,
                  {"kind": "fixed", "pence": 40000})
        assert transfer_rules.evaluate(session, r, TODAY) == 40000

    def test_income_pct_of_prior_period(
            self, session: Session, household: Household,
            accounts: tuple[Account, Account]) -> None:
        src, _ = accounts
        r = _rule(session, household, accounts,
                  {"kind": "income_pct", "bps": 5000})
        # £3,000 salary into source during Mar 1 → Apr 1 window
        session.add(Transaction(
            account_id=src.id, date=date(2025, 3, 15),
            amount_pence=300_000, payee="Salary",
            source="manual"))
        session.add(Transaction(
            account_id=src.id, date=date(2025, 3, 20),
            amount_pence=-50_000, payee="Spend",
            source="manual"))
        session.flush()
        assert transfer_rules.evaluate(
            session, r, date(2025, 4, 1)) == 150_000

    def test_budget_line_uses_effective_pence(
            self, session: Session, household: Household,
            accounts: tuple[Account, Account]) -> None:
        cat = Category(household_id=household.id, name="Food")
        session.add(cat)
        session.flush()
        b = Budget(household_id=household.id, name="April",
                   period_type="monthly",
                   start_date=date(2025, 4, 1),
                   end_date=date(2025, 4, 30),
                   status=BudgetStatus.approved)
        session.add(b)
        session.flush()
        session.add(BudgetLine(
            budget_id=b.id, category_id=cat.id,
            side=BudgetLineSide.expense,
            original_pence=80_000, revised_pence=90_000,
            revision_reason="prices"))
        session.flush()
        r = _rule(session, household, accounts,
                  {"kind": "budget_line", "category_id": cat.id})
        assert transfer_rules.evaluate(
            session, r, date(2025, 4, 15)) == 90_000

    def test_budget_line_none_without_covering_budget(
            self, session: Session, household: Household,
            accounts: tuple[Account, Account]) -> None:
        r = _rule(session, household, accounts,
                  {"kind": "budget_line", "category_id": 9})
        assert transfer_rules.evaluate(
            session, r, date(2025, 4, 15)) is None


class TestMaterialise:
    def test_two_legs_per_occurrence(
            self, session: Session, household: Household,
            accounts: tuple[Account, Account]) -> None:
        src, dst = accounts
        r = _rule(session, household, accounts,
                  {"kind": "fixed", "pence": 40000})
        n = schedule.rematerialize_transfer_rule(
            session, r, today=TODAY)
        rows = _rows(session, r)
        assert n == len(rows)
        occ = rows[0].expected_date
        legs = {m.account_id: m.amount_pence
                for m in rows if m.expected_date == occ}
        assert legs == {src.id: -40000, dst.id: 40000}
        assert all(m.confidence is Confidence.scheduled for m in rows)

    def test_derived_amounts_estimated(
            self, session: Session, household: Household,
            accounts: tuple[Account, Account]) -> None:
        r = _rule(session, household, accounts,
                  {"kind": "income_pct", "bps": 1000})
        schedule.rematerialize_transfer_rule(session, r, today=TODAY)
        assert all(m.confidence is Confidence.estimated
                   for m in _rows(session, r))

    def test_spec_edit_regenerates_future_only(
            self, session: Session, household: Household,
            accounts: tuple[Account, Account]) -> None:
        r = _rule(session, household, accounts,
                  {"kind": "fixed", "pence": 40000})
        r.start_date = date(2025, 1, 1)
        schedule.rematerialize_transfer_rule(
            session, r, today=date(2025, 3, 1))
        past = [m for m in _rows(session, r)
                if m.expected_date < TODAY]
        assert past, "expected historical rows pre-edit"
        past_ids = {m.id for m in past}
        r.amount_spec = json.dumps({"kind": "fixed", "pence": 55000})
        schedule.rematerialize_transfer_rule(session, r, today=TODAY)
        after = _rows(session, r)
        kept = {m.id for m in after if m.expected_date < TODAY}
        assert kept == past_ids  # history untouched
        future = [m for m in after if m.expected_date >= TODAY]
        assert all(abs(m.amount_pence) == 55000 for m in future)

    def test_deactivate_drops_future(
            self, session: Session, household: Household,
            accounts: tuple[Account, Account]) -> None:
        r = _rule(session, household, accounts,
                  {"kind": "fixed", "pence": 40000})
        schedule.rematerialize_transfer_rule(session, r, today=TODAY)
        r.active = False
        n = schedule.rematerialize_transfer_rule(
            session, r, today=TODAY)
        assert n == 0
        assert all(m.expected_date < TODAY for m in _rows(session, r))

    def test_idempotent(self, session: Session, household: Household,
                        accounts: tuple[Account, Account]) -> None:
        r = _rule(session, household, accounts,
                  {"kind": "fixed", "pence": 40000})
        n1 = schedule.rematerialize_transfer_rule(
            session, r, today=TODAY)
        n2 = schedule.rematerialize_transfer_rule(
            session, r, today=TODAY)
        assert n1 == n2 == len(_rows(session, r))

    def test_matched_rows_repriced_not_deleted(
            self, session: Session, household: Household,
            accounts: tuple[Account, Account]) -> None:
        """Editing a rule after a member paid must not 409 (#293): the
        matched row is FK-bound to expected_match, so it's repriced in
        place and the match shows the new outstanding delta."""
        _, dst = accounts
        r = _rule(session, household, accounts,
                  {"kind": "fixed", "pence": 80000})
        schedule.rematerialize_transfer_rule(session, r, today=TODAY)
        in_leg = next(m for m in _rows(session, r)
                      if m.account_id == dst.id
                      and m.expected_date >= TODAY)
        tx = Transaction(account_id=dst.id, date=in_leg.expected_date,
                         amount_pence=80000, payee="Share in",
                         source="manual")
        session.add(tx)
        session.flush()
        session.add(ExpectedMatch(movement_id=in_leg.id,
                                  transaction_id=tx.id,
                                  matched_pence=80000, manual=False))
        session.flush()
        r.amount_spec = json.dumps({"kind": "fixed", "pence": 90000})
        # raised IntegrityError before the fix — the delete hit the FK.
        schedule.rematerialize_transfer_rule(session, r, today=TODAY)
        session.flush()
        kept = next(m for m in _rows(session, r) if m.id == in_leg.id)
        assert kept.amount_pence == 90000
        s = contributions.status(session, kept, TODAY)
        assert s.matched_pence == 80000
        assert s.outstanding_pence == 10000
        # unmatched legs on other dates still regenerate normally
        assert all(abs(m.amount_pence) == 90000
                   for m in _rows(session, r)
                   if m.expected_date >= TODAY)

    def test_deactivate_keeps_matched_rows(
            self, session: Session, household: Household,
            accounts: tuple[Account, Account]) -> None:
        """Pausing a rule drops future expectations but keeps rows a
        real payment already matched — they were met obligations."""
        _, dst = accounts
        r = _rule(session, household, accounts,
                  {"kind": "fixed", "pence": 40000})
        schedule.rematerialize_transfer_rule(session, r, today=TODAY)
        in_leg = next(m for m in _rows(session, r)
                      if m.account_id == dst.id
                      and m.expected_date >= TODAY)
        tx = Transaction(account_id=dst.id, date=in_leg.expected_date,
                         amount_pence=40000, payee="In", source="manual")
        session.add(tx)
        session.flush()
        session.add(ExpectedMatch(movement_id=in_leg.id,
                                  transaction_id=tx.id,
                                  matched_pence=40000, manual=False))
        session.flush()
        r.active = False
        n = schedule.rematerialize_transfer_rule(
            session, r, today=TODAY)
        assert n == 0
        kept = [m for m in _rows(session, r) if m.expected_date >= TODAY]
        assert [m.id for m in kept] == [in_leg.id]


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load(
            {"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def _setup(self, client: TestClient) -> tuple[dict, int, int]:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        src = client.post("/api/v1/accounts", json={
            "name": "Personal", "type": "personal",
            "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        dst = client.post("/api/v1/accounts", json={
            "name": "Joint", "type": "joint", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        return h, src, dst

    def test_crud_and_legs(self, client: TestClient) -> None:
        h, src, dst = self._setup(client)
        r = client.post("/api/v1/transfer-rules", headers=h, json={
            "name": "Bills", "source_account_id": src,
            "dest_account_id": dst,
            "amount_spec": {"kind": "fixed", "pence": 50000},
            "frequency": "monthly", "day_of_month": 1,
            "start_date": "2025-01-01"})
        assert r.status_code == 201, r.json()
        rid = r.json()["id"]
        rows = client.get("/api/v1/expected-movements", headers=h,
                          params={"start": "2025-01-01",
                                  "end": "2026-12-31"}).json()
        legs = [m for m in rows
                if m["source_type"] == "transfer_rule"
                and m["source_id"] == rid]
        assert legs
        assert {m["account_id"] for m in legs} == {src, dst}
        # patch spec → future legs regenerate
        r = client.patch(f"/api/v1/transfer-rules/{rid}", headers=h,
                         json={"amount_spec": {"kind": "fixed",
                                               "pence": 60000}})
        assert r.status_code == 200
        rows = client.get("/api/v1/expected-movements", headers=h,
                          params={"start": "2025-01-01",
                                  "end": "2026-12-31"}).json()
        future = [m for m in rows
                  if m["source_type"] == "transfer_rule"
                  and m["source_id"] == rid
                  and m["expected_date"] >= date.today().isoformat()]
        assert {abs(m["amount_pence"]) for m in future} == {60000}

    def test_patch_after_match_reprices_leg(
            self, client: TestClient) -> None:
        """Regression for the split editor (#293): PATCHing a rule whose
        current-month leg was already matched used to 409 on the FK."""
        h, src, dst = self._setup(client)
        rid = client.post("/api/v1/transfer-rules", headers=h, json={
            "name": "Share", "source_account_id": src,
            "dest_account_id": dst,
            "amount_spec": {"kind": "fixed", "pence": 80000},
            "frequency": "monthly", "day_of_month": 1,
            "start_date": "2025-01-01"}).json()["id"]
        rows = client.get("/api/v1/expected-movements", headers=h,
                          params={"start": date.today().isoformat(),
                                  "end": "2027-12-31"}).json()
        in_leg = next(m for m in rows
                      if m["source_id"] == rid
                      and m["account_id"] == dst
                      and m["amount_pence"] > 0)
        tx = client.post("/api/v1/transactions", headers=h, json={
            "account_id": dst, "date": in_leg["expected_date"],
            "payee": "Share in", "amount": "800"}).json()
        m = client.post(
            f"/api/v1/expected-movements/{in_leg['id']}/matches",
            headers=h, json={"transaction_id": tx["id"]})
        assert m.status_code == 201, m.json()
        r = client.patch(f"/api/v1/transfer-rules/{rid}", headers=h,
                         json={"amount_spec": {"kind": "fixed",
                                               "pence": 90000}})
        assert r.status_code == 200
        rows = client.get("/api/v1/expected-movements", headers=h,
                          params={"start": date.today().isoformat(),
                                  "end": "2027-12-31"}).json()
        leg = next(m for m in rows if m["id"] == in_leg["id"])
        assert leg["amount_pence"] == 90000
        st = client.get("/api/v1/expected-movements/contributions",
                        headers=h,
                        params={"start": date.today().isoformat(),
                                "end": "2027-12-31"}).json()
        s = next(x for x in st if x["movement_id"] == in_leg["id"])
        assert s["matched_pence"] == 80000
        assert s["outstanding_pence"] == 10000

    def test_same_account_rejected(self, client: TestClient) -> None:
        h, src, _ = self._setup(client)
        r = client.post("/api/v1/transfer-rules", headers=h, json={
            "name": "Bad", "source_account_id": src,
            "dest_account_id": src,
            "amount_spec": {"kind": "fixed", "pence": 1},
            "frequency": "monthly", "day_of_month": 1,
            "start_date": "2025-01-01"})
        assert r.status_code == 422

    def test_cross_household_404(self, client: TestClient) -> None:
        h, src, dst = self._setup(client)
        r = client.post("/api/v1/transfer-rules", headers=h, json={
            "name": "R", "source_account_id": src,
            "dest_account_id": dst,
            "amount_spec": {"kind": "fixed", "pence": 100},
            "frequency": "monthly", "day_of_month": 1,
            "start_date": "2025-01-01"})
        rid = r.json()["id"]
        hid2 = client.post("/api/v1/households",
                           json={"name": "Other"}).json()["id"]
        h2 = {"X-Household-Id": str(hid2)}
        assert client.get(
            f"/api/v1/transfer-rules/{rid}/evaluate",
            headers=h2, params={"on": "2025-04-01"}).status_code == 404

    def test_budget_approval_recomputes(
            self, client: TestClient) -> None:
        h, src, dst = self._setup(client)
        cat = client.post("/api/v1/categories", headers=h, json={
            "name": "Food"}).json()["id"]
        r = client.post("/api/v1/transfer-rules", headers=h, json={
            "name": "Food money", "source_account_id": src,
            "dest_account_id": dst,
            "amount_spec": {"kind": "budget_line",
                            "category_id": cat},
            "frequency": "monthly", "day_of_month": 1,
            "start_date": "2025-01-01"})
        rid = r.json()["id"]
        # no covering budget → no legs yet
        ev = client.get(f"/api/v1/transfer-rules/{rid}/evaluate",
                        headers=h,
                        params={"on": "2025-05-01"}).json()
        assert ev["amount_pence"] is None
        # approve a budget covering May with a food line
        b = client.post("/api/v1/budgets", headers=h, json={
            "name": "May", "period_type": "monthly",
            "start_date": "2025-05-01",
            "end_date": "2025-05-31"}).json()["id"]
        client.post(f"/api/v1/budgets/{b}/lines", headers=h, json={
            "category_id": cat, "side": "expense", "amount": 75000})
        ap = client.post(f"/api/v1/budgets/{b}/approve", headers=h)
        assert ap.status_code == 200
        ev = client.get(f"/api/v1/transfer-rules/{rid}/evaluate",
                        headers=h,
                        params={"on": "2025-05-01"}).json()
        assert ev["amount_pence"] == 75000
        assert ev["confidence"] == "estimated"
