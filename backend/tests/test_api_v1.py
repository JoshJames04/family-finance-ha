"""Contract tests for /api/v1 (issue #42).

Covers CRUD per resource, pagination/filtering, Money boundary rules,
domain-rule → 409 mapping, household scope filtering, audit emission.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app
from app.persistence import session as ps
from app.persistence.models import AuditEvent


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
    with TestClient(create_app(settings)) as c:
        yield c


def _mk_household(client: TestClient) -> int:
    r = client.post("/api/v1/households", json={"name": "Home"})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def _mk_account(client: TestClient, name: str = "Joint",
                pence: int = 1000) -> int:
    r = client.post("/api/v1/accounts", json={
        "name": name, "type": "joint", "opening_balance": pence,
        "opening_date": "2026-01-01"})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


class TestHouseholdsAndAccounts:
    def test_household_flow(self, client: TestClient) -> None:
        hid = _mk_household(client)
        r = client.get("/api/v1/households")
        assert r.status_code == 200
        assert r.json()["total"] >= 1
        assert any(h["id"] == hid for h in r.json()["items"])

    def test_account_crud_and_balance(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client, pence=25000)
        assert client.get(
            f"/api/v1/accounts/{aid}").json()["name"] == "Joint"
        bal = client.get(f"/api/v1/accounts/{aid}/balance").json()
        assert bal["balance_pence"] == 25000
        r = client.patch(f"/api/v1/accounts/{aid}",
                         json={"name": "Renamed"})
        assert r.json()["name"] == "Renamed"

    def test_archive_excludes_default_list(
        self, client: TestClient
    ) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        client.post(f"/api/v1/accounts/{aid}/archive")
        assert client.get("/api/v1/accounts").json()["total"] == 0
        assert client.get(
            "/api/v1/accounts?include_archived=true").json()["total"] == 1

    def test_delete_nonempty_409(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)  # has opening txn → non-empty
        r = client.delete(f"/api/v1/accounts/{aid}")
        assert r.status_code == 409

    def test_merge_moves_history(self, client: TestClient) -> None:
        _mk_household(client)
        a, b = _mk_account(client, "A", 500), _mk_account(client, "B", 100)
        r = client.post(f"/api/v1/accounts/{a}/merge-into/{b}")
        assert r.status_code == 200
        assert client.get(
            f"/api/v1/accounts/{b}/balance").json()["balance_pence"] == 600

    def test_openapi_covers_endpoints(self, client: TestClient) -> None:
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        for p in ("/api/v1/accounts", "/api/v1/categories",
                  "/api/v1/transactions", "/api/v1/transfers",
                  "/api/v1/commitments", "/api/v1/households",
                  "/api/v1/transactions/bulk"):
            assert p in paths, p


class TestCategories:
    def test_create_and_hierarchy(self, client: TestClient) -> None:
        _mk_household(client)
        root = client.post("/api/v1/categories",
                           json={"name": "Food"}).json()
        child = client.post("/api/v1/categories", json={
            "name": "Groceries", "parent_id": root["id"]}).json()
        assert child["parent_id"] == root["id"]

    def test_cycle_409(self, client: TestClient) -> None:
        _mk_household(client)
        a = client.post("/api/v1/categories",
                        json={"name": "A"}).json()
        b = client.post("/api/v1/categories",
                        json={"name": "B", "parent_id": a["id"]}).json()
        r = client.patch(f"/api/v1/categories/{a['id']}",
                         json={"parent_id": b["id"]})
        assert r.status_code == 409

    def test_delete_archives(self, client: TestClient) -> None:
        _mk_household(client)
        c = client.post("/api/v1/categories",
                        json={"name": "Old"}).json()
        assert client.delete(
            f"/api/v1/categories/{c['id']}").status_code == 204
        assert client.get(
            "/api/v1/categories?include_archived=true"
        ).json()["items"][0]["archived"] is True


class TestTransactions:
    def _mk(self, client: TestClient, aid: int, pence: int = -500,
            payee: str = "Tesco", **kw) -> dict:
        r = client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2026-05-10", "amount": pence,
            "payee": payee, **kw})
        assert r.status_code == 201, r.text
        return r.json()

    def test_create_list_filter_paginate(
        self, client: TestClient
    ) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        for i in range(5):
            self._mk(client, aid, payee=f"Shop{i}")
        page1 = client.get("/api/v1/transactions?size=2&page=1").json()
        assert page1["total"] == 6  # 5 + opening balance
        assert len(page1["items"]) == 2
        r = client.get("/api/v1/transactions?q=Shop1").json()
        assert r["total"] == 1
        r = client.get(
            "/api/v1/transactions?date_from=2026-06-01").json()
        assert r["total"] == 0

    def test_float_rejected(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        r = client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2026-05-10",
            "amount": 12.34, "payee": "x"})
        assert r.status_code == 422

    def test_status_flow_and_lock(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        t = self._mk(client, aid)
        tid = t["id"]
        assert client.patch(f"/api/v1/transactions/{tid}",
                            json={"status": "cleared"}
                            ).status_code == 200
        # invalid skip pending->reconciled impossible from cleared → ok
        assert client.patch(f"/api/v1/transactions/{tid}",
                            json={"status": "reconciled"}
                            ).status_code == 200
        # reconciled now immutable
        r = client.patch(f"/api/v1/transactions/{tid}",
                         json={"payee": "edited"})
        assert r.status_code == 409

    def test_bulk_categorise(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        cat = client.post("/api/v1/categories",
                          json={"name": "Food"}).json()
        t1 = self._mk(client, aid)
        t2 = self._mk(client, aid, payee="Asda")
        r = client.post("/api/v1/transactions/bulk", json={
            "ids": [t1["id"], t2["id"], 99999],
            "category_id": cat["id"]})
        assert r.status_code == 200
        body = r.json()
        assert sorted(body["applied"]) == sorted([t1["id"], t2["id"]])
        assert len(body["rejected"]) == 1


class TestTransfersAndCommitments:
    def test_transfer_roundtrip(self, client: TestClient) -> None:
        _mk_household(client)
        a, b = _mk_account(client, "A"), _mk_account(client, "B")
        d = client.post("/api/v1/transactions", json={
            "account_id": a, "date": "2026-05-01", "amount": -5000,
            "payee": "move"}).json()
        c = client.post("/api/v1/transactions", json={
            "account_id": b, "date": "2026-05-01", "amount": 5000,
            "payee": "move"}).json()
        r = client.post("/api/v1/transfers", json={
            "debit_transaction_id": d["id"],
            "credit_transaction_id": c["id"]})
        assert r.status_code == 201, r.text
        assert sorted(r.json()["legs"]) == sorted([d["id"], c["id"]])

    def test_commitment_materialise(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        c = client.post("/api/v1/commitments", json={
            "name": "Rent", "account_id": aid, "amount": -120000,
            "frequency": "monthly", "next_date": "2026-01-01"}).json()
        r = client.post(
            f"/api/v1/commitments/{c['id']}/materialise?"
            "horizon=2026-03-31")
        assert r.status_code == 200, r.text
        assert len(r.json()["created"]) == 3

    def test_commitment_xor_amount(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        r = client.post("/api/v1/commitments", json={
            "name": "Bad", "account_id": aid, "frequency": "monthly",
            "next_date": "2026-01-01"})
        assert r.status_code == 422


class TestScopeAndAudit:
    def test_cross_household_isolation(self, client: TestClient) -> None:
        h1 = _mk_household(client)
        aid = _mk_account(client)
        h2 = client.post("/api/v1/households",
                         json={"name": "Other"}).json()["id"]
        r = client.get(f"/api/v1/accounts/{aid}",
                       headers={"X-Household-Id": str(h2)})
        assert r.status_code == 404
        r = client.get(f"/api/v1/accounts/{aid}",
                       headers={"X-Household-Id": str(h1)})
        assert r.status_code == 200
        r = client.get("/api/v1/accounts/999999",
                       headers={"X-Household-Id": "999"})
        assert r.status_code == 404

    def test_audit_events_emitted(
        self, client: TestClient
    ) -> None:
        _mk_household(client)
        _mk_account(client)
        assert ps._session_factory is not None
        with ps._session_factory() as s:
            rows = s.query(AuditEvent).all()
        assert len(rows) >= 2
        assert {r.entity_type for r in rows} >= {"household", "account"}
        assert all(r.actor == "anonymous" for r in rows)
