"""Issue #71 — manual-accounts feature parity.

Proves every Epic 3/5/7 workflow completes on a household whose only
accounts are manual (``institution_ref`` NULL, ``source=manual``) — no
bank connection exists anywhere in the product yet, and nothing in the
API surface must require one.
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
                      json={"name": "Manual H"}).json()["id"]
    return {"X-Household-Id": str(hid)}


def _acct(client: TestClient, h: dict[str, str], name: str = "Joint",
          balance: int = 0) -> int:
    r = client.post("/api/v1/accounts", json={
        "name": name, "type": "joint", "opening_balance": balance,
        "opening_date": "2026-01-01"}, headers=h)
    assert r.status_code == 201
    return int(r.json()["id"])


class TestManualAccountLifecycle:
    def test_full_crud(self, client: TestClient, h: dict) -> None:
        aid = _acct(client, h, "Everyday", 150000)
        a = client.get("/api/v1/accounts", headers=h).json()["items"][0]
        assert a["institution_ref"] is None  # manual: no connection
        r = client.patch(f"/api/v1/accounts/{aid}",
                         json={"name": "Renamed"}, headers=h)
        assert r.status_code == 200
        client.post(f"/api/v1/accounts/{aid}/archive", headers=h)
        client.post(f"/api/v1/accounts/{aid}/reopen", headers=h)
        a = client.get("/api/v1/accounts", headers=h
                       ).json()["items"][0]
        assert a["archived"] is False

    def test_no_sync_affordances(self, client: TestClient,
                                 h: dict) -> None:
        """Manual accounts expose no sync-only fields or bank
        affordances. Generic /sync monitoring routes (issue #67)
        are legitimate; Starling-specific wiring is not yet."""
        _acct(client, h)
        a = client.get("/api/v1/accounts", headers=h
                       ).json()["items"][0]
        assert not any("sync" in k or "connection" in k for k in a)
        spec = client.get("/openapi.json").json()
        assert not any("starling" in p for p in spec["paths"])


class TestManualTransactions:
    def test_entry_edit_status_lifecycle(
            self, client: TestClient, h: dict) -> None:
        aid = _acct(client, h)
        tid = client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2026-05-01", "amount": -2599,
            "payee": "Tesco"}, headers=h).json()["id"]
        t = client.get(f"/api/v1/transactions/{tid}",
                       headers=h).json()
        assert t["source"] == "manual" and t["status"] == "pending"
        for status in ("cleared", "reconciled"):
            r = client.patch(f"/api/v1/transactions/{tid}",
                             json={"status": status}, headers=h)
            assert r.status_code == 200
        # void is reachable too (reversal path)
        tid2 = client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2026-05-02", "amount": -100,
            "payee": "Mistake"}, headers=h).json()["id"]
        r = client.patch(f"/api/v1/transactions/{tid2}",
                         json={"status": "void"}, headers=h)
        assert r.status_code == 200

    def test_dedup_on_external_ref(self, client: TestClient,
                                   h: dict) -> None:
        """(account, source, external_ref) uniqueness still guards
        manual re-entry when a ref is supplied."""
        aid = _acct(client, h)
        body = {"account_id": aid, "date": "2026-05-01",
                "amount": -50, "payee": "Dup", "external_ref": "r1"}
        assert client.post("/api/v1/transactions", json=body,
                           headers=h).status_code == 201
        r = client.post("/api/v1/transactions", json=body, headers=h)
        assert r.status_code == 409
        # NULL external_ref never dedups (two identical manual rows ok)
        body.pop("external_ref")
        assert client.post("/api/v1/transactions", json=body,
                           headers=h).status_code == 201
        assert client.post("/api/v1/transactions", json=body,
                           headers=h).status_code == 201


class TestManualWorkflows:
    def test_transfer_between_manual_accounts(
            self, client: TestClient, h: dict) -> None:
        a, b = _acct(client, h, "A"), _acct(client, h, "B")
        d = client.post("/api/v1/transactions", json={
            "account_id": a, "date": "2026-05-10", "amount": -5000,
            "payee": "to B"}, headers=h).json()
        c = client.post("/api/v1/transactions", json={
            "account_id": b, "date": "2026-05-10", "amount": 5000,
            "payee": "from A"}, headers=h).json()
        r = client.post("/api/v1/transfers", json={
            "debit_transaction_id": d["id"],
            "credit_transaction_id": c["id"]}, headers=h)
        assert r.status_code == 201
        assert sorted(r.json()["legs"]) == sorted([d["id"], c["id"]])

    def test_commitment_materialises_manual(
            self, client: TestClient, h: dict) -> None:
        aid = _acct(client, h)
        cid = client.post("/api/v1/commitments", json={
            "name": "Rent", "account_id": aid, "amount": -85000,
            "frequency": "monthly", "next_date": "2026-05-01"},
            headers=h).json()["id"]
        r = client.post(f"/api/v1/commitments/{cid}/materialise"
                        "?horizon=2026-06-01", headers=h)
        assert r.status_code == 200 and len(r.json()["created"]) >= 1

    def test_refund_linking(self, client: TestClient,
                            h: dict) -> None:
        aid = _acct(client, h)
        orig = client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2026-05-01", "amount": -3000,
            "payee": "Shop"}, headers=h).json()["id"]
        r = client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2026-05-05", "amount": 3000,
            "payee": "Shop refund", "refund_of_id": orig}, headers=h)
        assert r.status_code == 201

    def test_budget_actuals_from_manual_data(
            self, client: TestClient, h: dict) -> None:
        """Epic 5 reporting works with nothing but manual rows."""
        aid = _acct(client, h)
        cat = client.post("/api/v1/categories", json={"name": "Food"},
                          headers=h).json()["id"]
        client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2026-05-03", "amount": -4200,
            "payee": "Groceries", "category_id": cat}, headers=h)
        bid = client.post("/api/v1/budgets", json={
            "name": "May", "period_type": "monthly",
            "start_date": "2026-05-01", "end_date": "2026-05-31"},
            headers=h).json()["id"]
        client.post(f"/api/v1/budgets/{bid}/lines", json={
            "category_id": cat, "side": "expense", "amount": 50000},
            headers=h)
        client.post(f"/api/v1/budgets/{bid}/approve", headers=h)
        rep = client.get(f"/api/v1/budgets/{bid}/report",
                         headers=h).json()
        line = rep["lines"][0]
        assert line["actual_pence"] == 4200  # spend reported positive
        assert line["variance_pence"] == 45800  # 50000 - 4200 under

    def test_reconnected_account_resumes(
            self, client: TestClient, h: dict) -> None:
        """'Disconnected' = institution_ref cleared; setting it back on
        the same account must not break any workflow."""
        aid = _acct(client, h)
        r = client.patch(f"/api/v1/accounts/{aid}",
                         json={"institution_ref": "acc_123"},
                         headers=h)
        assert r.status_code == 200
        # manual entry still works on the reconnected account
        r = client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2026-05-01", "amount": -10,
            "payee": "Manual"}, headers=h)
        assert r.status_code == 201
        r = client.patch(f"/api/v1/accounts/{aid}",
                         json={"institution_ref": None}, headers=h)
        assert r.status_code == 200
