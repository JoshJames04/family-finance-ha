"""Issue #75 — overdraft warnings + suggested transfer sources.

AC: projected -£50 dip → warning states amount, date and suggested
source; warnings resolve automatically when inputs change.
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


def _account(session: Session, household: Household, name: str,
             opening: int, min_bal: int | None = None) -> Account:
    a = Account(household_id=household.id, name=name, type="personal",
                opening_balance_pence=0,
                opening_date=date(2025, 1, 1),
                min_balance_pence=min_bal)
    session.add(a)
    session.flush()
    if opening:
        session.add(Transaction(
            account_id=a.id, date=date(2025, 4, 1),
            amount_pence=opening, payee="seed",
            status=TransactionStatus.cleared, source="manual"))
        session.flush()
    return a


def _expect(session: Session, household: Household, a: Account,
            on: date, pence: int, label: str = "x") -> ExpectedMovement:
    m = ExpectedMovement(
        household_id=household.id, account_id=a.id, expected_date=on,
        amount_pence=pence, label=label,
        confidence=Confidence.scheduled,
        source_type=ExpectedSource.commitment, source_id=1)
    session.add(m)
    session.flush()
    return m


class TestWarnings:
    def test_dip_warns_amount_date_source(
            self, session: Session, household: Household) -> None:
        """AC: -£50 dip on dd/MM → warning carries amount, date, and a
        suggested source covering it."""
        bills = _account(session, household, "Bills", 30_000)
        _account(session, household, "Savings", 80_000)
        _expect(session, household, bills, date(2025, 4, 15), -80_000,
                "big bill")

        warns = projection.warnings(session, household.id, 30,
                                    from_date=TODAY)
        assert len(warns) == 1
        w = warns[0]
        assert w.account_id == bills.id
        assert w.first_dip_date == date(2025, 4, 15)
        assert w.min_balance_pence == -50_000
        assert w.deficit_pence == 50_000
        assert [s.name for s in w.suggested_sources] == ["Savings"]
        assert w.suggested_sources[0].available_pence == 80_000

    def test_threshold_floor_not_just_zero(
            self, session: Session, household: Household) -> None:
        """A balance above zero but below min_balance_pence warns too."""
        a = _account(session, household, "A", 40_000, min_bal=25_000)
        _expect(session, household, a, date(2025, 4, 12), -30_000)
        warns = projection.warnings(session, household.id, 30,
                                    from_date=TODAY)
        assert len(warns) == 1
        w = warns[0]
        assert w.floor_pence == 25_000
        assert w.min_balance_pence == 10_000
        assert w.deficit_pence == 15_000

    def test_rescue_chain_multi_account(
            self, session: Session, household: Household) -> None:
        """No single source covers the deficit → chain lists the top
        candidates until the deficit is covered."""
        needy = _account(session, household, "Needy", 0)
        _account(session, household, "Small", 20_000)
        _account(session, household, "Large", 100_000)
        _expect(session, household, needy, date(2025, 4, 15), -60_000)

        warns = projection.warnings(session, household.id, 30,
                                    from_date=TODAY)
        w = warns[0]
        assert w.deficit_pence == 60_000
        names = [s.name for s in w.suggested_sources]
        # Large first (biggest surplus); its £100k covers the £60k
        # deficit alone — chain stops
        assert names == ["Large"]

    def test_rescue_chain_combines_when_needed(
            self, session: Session, household: Household) -> None:
        needy = _account(session, household, "Needy", 0)
        _account(session, household, "S1", 20_000)
        _account(session, household, "S2", 30_000)
        _expect(session, household, needy, date(2025, 4, 15), -45_000)
        w = projection.warnings(session, household.id, 30,
                                from_date=TODAY)[0]
        assert [s.name for s in w.suggested_sources] == ["S2", "S1"]
        total = sum(s.available_pence for s in w.suggested_sources)
        assert total >= w.deficit_pence

    def test_source_surplus_respects_own_floor(
            self, session: Session, household: Household) -> None:
        needy = _account(session, household, "Needy", 0)
        _account(session, household, "Guarded", 30_000, min_bal=25_000)
        _expect(session, household, needy, date(2025, 4, 15), -40_000)
        w = projection.warnings(session, household.id, 30,
                                from_date=TODAY)[0]
        assert w.suggested_sources[0].available_pence == 5_000

    def test_resolves_when_inputs_change(
            self, session: Session, household: Household) -> None:
        """AC: warnings are computed — fix the ledger and they go."""
        a = _account(session, household, "A", 10_000)
        m = _expect(session, household, a, date(2025, 4, 15), -60_000)
        assert projection.warnings(session, household.id, 30,
                                   from_date=TODAY)
        # input change: extra income lands before the dip
        session.add(Transaction(
            account_id=a.id, date=date(2025, 4, 12),
            amount_pence=80_000, payee="bonus",
            status=TransactionStatus.cleared, source="manual"))
        session.flush()
        assert projection.warnings(session, household.id, 30,
                                   from_date=TODAY) == []
        # and removing the expectation also resolves it
        session.delete(m)
        session.flush()
        assert projection.warnings(session, household.id, 30,
                                   from_date=TODAY) == []

    def test_no_dip_no_warning(
            self, session: Session, household: Household) -> None:
        a = _account(session, household, "A", 50_000)
        _expect(session, household, a, date(2025, 4, 15), -20_000)
        assert projection.warnings(session, household.id, 30,
                                   from_date=TODAY) == []

    def test_archived_accounts_skipped(
            self, session: Session, household: Household) -> None:
        a = _account(session, household, "A", 0)
        a.archived = True
        session.flush()
        _expect(session, household, a, date(2025, 4, 15), -60_000)
        assert projection.warnings(session, household.id, 30,
                                   from_date=TODAY) == []


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_warnings_endpoint(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        needy = client.post("/api/v1/accounts", json={
            "name": "Needy", "type": "personal", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        client.post("/api/v1/accounts", json={
            "name": "Saver", "type": "personal",
            "opening_balance": 80_000,
            "opening_date": "2025-01-01"}, headers=h)
        future = (date.today() + timedelta(days=5)).isoformat()
        client.post("/api/v1/expected-movements", json={
            "expected_date": future, "amount": -50_000,
            "account_id": needy, "label": "Bill"}, headers=h)
        res = client.get("/api/v1/forecast/warnings", headers=h)
        assert res.status_code == 200
        body = res.json()
        assert len(body) == 1
        assert body[0]["account_id"] == needy
        assert body[0]["deficit_pence"] == 50_000
        assert [s["name"] for s in body[0]["suggested_sources"]] == \
            ["Saver"]
