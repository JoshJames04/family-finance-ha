"""Issue #95 — manual balance verification + correction workflow.

A verification records asserted vs ledger-derived balance; corrections
are NEW ledger rows (source ``manual_verification``) — history is never
edited and the adjustment explains itself via the verification link.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(
            Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
        yield c


@pytest.fixture()
def h(client: TestClient) -> dict[str, str]:
    hid = client.post("/api/v1/households",
                      json={"name": "Verify H"}).json()["id"]
    return {"X-Household-Id": str(hid)}


def _acct(client: TestClient, h: dict[str, str], balance: int = 0) -> int:
    r = client.post("/api/v1/accounts", json={
        "name": "Everyday", "type": "personal",
        "opening_balance": balance, "opening_date": "2026-01-01"},
        headers=h)
    assert r.status_code == 201
    return int(r.json()["id"])


def _txn(client: TestClient, h: dict[str, str], acct: int,
         amount: int) -> None:
    r = client.post("/api/v1/transactions", json={
        "account_id": acct, "date": "2026-02-01", "amount": amount,
        "payee": "Spend"}, headers=h)
    assert r.status_code == 201


def _verify(client: TestClient, h: dict[str, str], acct: int,
            asserted: int, **kw: object) -> dict:
    r = client.post(f"/api/v1/accounts/{acct}/verify-balance",
                    json={"asserted_balance": asserted,
                          "verified_date": "2026-03-01", **kw},
                    headers=h)
    assert r.status_code == 201, r.text
    return dict(r.json())


class TestVerification:
    def test_matching_balance_zero_variance(
            self, client: TestClient, h: dict) -> None:
        aid = _acct(client, h, 100_000)
        _txn(client, h, aid, -25_000)
        v = _verify(client, h, aid, 75_000)
        assert v["derived_pence"] == 75_000
        assert v["variance_pence"] == 0
        assert v["correction_txn_id"] is None

    def test_variance_recorded_without_correction(
            self, client: TestClient, h: dict) -> None:
        aid = _acct(client, h, 100_000)
        v = _verify(client, h, aid, 120_000)
        assert v["variance_pence"] == 20_000
        assert v["correction_txn_id"] is None
        # Ledger untouched — balance still derived-only.
        bal = client.get(f"/api/v1/accounts/{aid}/balance",
                         headers=h).json()
        assert bal["balance_pence"] == 100_000

    def test_correction_appends_adjustment(
            self, client: TestClient, h: dict) -> None:
        aid = _acct(client, h, 100_000)
        v = _verify(client, h, aid, 88_500, correct=True,
                    note="Statement shows £885.00")
        assert v["variance_pence"] == -11_500
        tid = v["correction_txn_id"]
        assert tid is not None
        # Derived balance now equals the asserted one.
        bal = client.get(f"/api/v1/accounts/{aid}/balance",
                         headers=h).json()
        assert bal["balance_pence"] == 88_500
        # The adjusting record explains itself.
        txns = client.get(f"/api/v1/accounts/{aid}/transactions",
                          headers=h).json()["items"]
        corr = next(t for t in txns if t["id"] == tid)
        assert corr["source"] == "manual_verification"
        assert corr["amount_pence"] == -11_500
        assert corr["external_ref"] == f"balver:{v['id']}"
        assert corr["notes"] == "Statement shows £885.00"

    def test_correction_not_duplicated_when_in_line(
            self, client: TestClient, h: dict) -> None:
        aid = _acct(client, h, 50_000)
        v = _verify(client, h, aid, 50_000, correct=True)
        assert v["correction_txn_id"] is None

    def test_history_never_edited(self, client: TestClient,
                                  h: dict) -> None:
        aid = _acct(client, h, 100_000)
        _txn(client, h, aid, -10_000)
        before = client.get(f"/api/v1/accounts/{aid}/transactions",
                            headers=h).json()["items"]
        _verify(client, h, aid, 70_000, correct=True)
        after = client.get(f"/api/v1/accounts/{aid}/transactions",
                           headers=h).json()["items"]
        assert len(after) == len(before) + 1
        pre = {t["id"]: t for t in before}
        for t in after:
            if t["id"] in pre:
                assert t == pre[t["id"]]


class TestReconciliationSurface:
    def test_verification_history_listed(
            self, client: TestClient, h: dict) -> None:
        aid = _acct(client, h, 100_000)
        _verify(client, h, aid, 90_000)
        _verify(client, h, aid, 90_000, correct=True)
        rows = client.get(f"/api/v1/accounts/{aid}/verifications",
                          headers=h).json()["items"]
        assert len(rows) == 2
        # Newest first; both saw -10k variance, only the 2nd corrected.
        assert rows[0]["variance_pence"] == -10_000
        assert rows[0]["correction_txn_id"] is not None
        assert rows[1]["variance_pence"] == -10_000
        assert rows[1]["correction_txn_id"] is None

    def test_verification_audited(self, client: TestClient,
                                  h: dict) -> None:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.persistence.models import AuditEvent
        aid = _acct(client, h, 100_000)
        _verify(client, h, aid, 95_000, correct=True)
        with Session(client.app.state.engine) as s:
            events = s.execute(select(AuditEvent)).scalars().all()
        assert any(e.action == "verify_balance"
                   and e.entity_type == "account"
                   and e.entity_id == aid for e in events)

    def test_cross_household_404(self, client: TestClient,
                                 h: dict) -> None:
        aid = _acct(client, h, 100_000)
        other = client.post("/api/v1/households",
                            json={"name": "Other"}).json()["id"]
        ho = {"X-Household-Id": str(other)}
        r = client.post(f"/api/v1/accounts/{aid}/verify-balance",
                        json={"asserted_balance": 1,
                              "verified_date": "2026-03-01"}, headers=ho)
        assert r.status_code == 404

    def test_negative_correction_downwards(
            self, client: TestClient, h: dict) -> None:
        """Asserted < derived also corrects (overstatement fix)."""
        aid = _acct(client, h, 10_000)
        _txn(client, h, aid, 5_000)  # derived 15_000
        v = _verify(client, h, aid, 12_000, correct=True)
        assert v["variance_pence"] == -3_000
        bal = client.get(f"/api/v1/accounts/{aid}/balance",
                         headers=h).json()
        assert bal["balance_pence"] == 12_000
