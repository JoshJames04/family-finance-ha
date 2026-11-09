"""Contract tests for /api/v1 (issue #42).

Covers CRUD per resource, pagination/filtering, Money boundary rules,
domain-rule → 409 mapping, household scope filtering, audit emission.
"""

from collections.abc import Iterator
from datetime import date
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

    def test_splits_put_and_validation(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        groceries = client.post("/api/v1/categories",
                                json={"name": "Groceries"}).json()
        gifts = client.post("/api/v1/categories",
                            json={"name": "Gifts"}).json()
        t = self._mk(client, aid, pence=-1299)
        r = client.put(f"/api/v1/transactions/{t['id']}/splits", json={
            "splits": [
                {"category_id": groceries["id"], "amount_pence": -800},
                {"category_id": gifts["id"], "amount_pence": -499}]})
        assert r.status_code == 200, r.text
        assert [(s["category_id"], s["amount_pence"]) for s in
                r.json()["splits"]] == [
            (groceries["id"], -800), (gifts["id"], -499)]
        bad = client.put(f"/api/v1/transactions/{t['id']}/splits", json={
            "splits": [
                {"category_id": groceries["id"], "amount_pence": -800},
                {"category_id": gifts["id"], "amount_pence": -400}]})
        assert bad.status_code == 422
        single = client.put(
            f"/api/v1/transactions/{t['id']}/splits", json={
                "splits": [{"category_id": groceries["id"],
                            "amount_pence": -1299}]})
        assert single.status_code == 422
        cleared = client.put(f"/api/v1/transactions/{t['id']}/splits",
                             json={"splits": []})
        assert cleared.status_code == 200
        assert cleared.json()["splits"] == []

    def test_splits_reconciled_must_correct(
            self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        cat = client.post("/api/v1/categories",
                          json={"name": "Food"}).json()
        t = self._mk(client, aid)
        assert client.patch(f"/api/v1/transactions/{t['id']}",
                            json={"status": "cleared"}).status_code == 200
        assert client.patch(f"/api/v1/transactions/{t['id']}",
                            json={"status": "reconciled"}
                            ).status_code == 200
        r = client.put(f"/api/v1/transactions/{t['id']}/splits", json={
            "splits": [
                {"category_id": cat["id"], "amount_pence": -300},
                {"category_id": cat["id"], "amount_pence": -200}]})
        assert r.status_code == 409
        corrected = client.post(
            f"/api/v1/transactions/{t['id']}/correct",
            json={"reason": "split it"})
        assert corrected.status_code == 200, corrected.text

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

    def test_commitment_patch(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        c = client.post("/api/v1/commitments", json={
            "name": "Broadband", "account_id": aid, "amount": -2800,
            "frequency": "monthly", "next_date": "2026-02-14",
            "day_of_month": 14}).json()
        r = client.patch(f"/api/v1/commitments/{c['id']}", json={
            "amount": -3200, "notes": "Tariff renewed"})
        assert r.status_code == 200, r.text
        assert r.json()["amount_pence"] == -3200
        assert r.json()["notes"] == "Tariff renewed"
        # amount <-> formula swap keeps the xor invariant
        r = client.patch(f"/api/v1/commitments/{c['id']}", json={
            "formula": "usage * 1.2"})
        assert r.json()["formula"] == "usage * 1.2"
        assert r.json()["amount_pence"] is None
        # patching amount back clears the formula
        r = client.patch(f"/api/v1/commitments/{c['id']}", json={
            "amount": -3000})
        assert r.json()["formula"] is None
        assert r.json()["amount_pence"] == -3000
        # clearing both is impossible via patch; invariant enforced
        r = client.patch(f"/api/v1/commitments/{c['id']}", json={
            "amount": None})
        assert r.status_code == 422

    def test_commitment_rejected_when_subscription_covers_payee(
            self, client: TestClient) -> None:
        """Issue #313 — the registers are either/or: a payee tracked
        as an active subscription cannot also become a bill."""
        _mk_household(client)
        aid = _mk_account(client)
        s = client.post("/api/v1/subscriptions", json={
            "payee": "IFTTT Pro", "frequency": "monthly",
            "expected": 299, "next_renewal_date": "2026-11-30"})
        assert s.status_code == 201, s.text
        r = client.post("/api/v1/commitments", json={
            "name": "ifttt pro", "account_id": aid, "amount": -299,
            "frequency": "monthly", "next_date": "2026-11-28"})
        assert r.status_code == 409

    def test_commitment_deactivate_activate(
        self, client: TestClient
    ) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        c = client.post("/api/v1/commitments", json={
            "name": "Gym", "account_id": aid, "amount": -2999,
            "frequency": "monthly", "next_date": "2020-01-01"}).json()
        r = client.delete(f"/api/v1/commitments/{c['id']}")
        assert r.status_code == 204
        items = client.get("/api/v1/commitments").json()["items"]
        assert items == []  # inactive filtered by default
        items = client.get(
            "/api/v1/commitments?include_inactive=true").json()["items"]
        assert len(items) == 1 and items[0]["active"] is False
        r = client.post(f"/api/v1/commitments/{c['id']}/activate")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["active"] is True
        # next_date rolled forward — no backdated burst on resume
        assert body["next_date"] >= date.today().isoformat()

    def test_commitment_permanent_delete(
        self, client: TestClient
    ) -> None:
        """#321: ungenerated commitments can be fully removed — the row
        and every expected-movement row go with it."""
        _mk_household(client)
        aid = _mk_account(client)
        c = client.post("/api/v1/commitments", json={
            "name": "Old Gym", "account_id": aid, "amount": -2999,
            "frequency": "monthly",
            "next_date": "2026-01-01"}).json()
        assert client.delete(
            f"/api/v1/commitments/{c['id']}?permanent=true"
        ).status_code == 204
        items = client.get(
            "/api/v1/commitments?include_inactive=true").json()["items"]
        assert items == []
        # row is really gone, not just paused — no expected rows left
        em = client.get("/api/v1/expected-movements"
                        "?start=2020-01-01&end=2030-12-31").json()
        assert em == []
        assert client.delete(
            f"/api/v1/commitments/{c['id']}").status_code == 404

    def test_commitment_permanent_delete_refused_when_generated(
        self, client: TestClient
    ) -> None:
        """#321: a commitment with generated transactions keeps its
        history — permanent delete 422s, pause still works."""
        _mk_household(client)
        aid = _mk_account(client)
        c = client.post("/api/v1/commitments", json={
            "name": "Rent", "account_id": aid, "amount": -120000,
            "frequency": "monthly",
            "next_date": "2026-01-01"}).json()
        r = client.post(
            f"/api/v1/commitments/{c['id']}/materialise?"
            "horizon=2026-03-31")
        assert r.status_code == 200
        r = client.delete(f"/api/v1/commitments/{c['id']}?permanent=true")
        assert r.status_code == 422
        assert client.delete(
            f"/api/v1/commitments/{c['id']}").status_code == 204


class TestPayees:
    """Canonical payee list + rename/merge (issue #276)."""

    def _mk(self, client: TestClient, aid: int, payee: str,
            status: str | None = None) -> dict:
        r = client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2026-05-10",
            "amount": -500, "payee": payee})
        assert r.status_code == 201, r.text
        body = r.json()
        if status:
            path = ["cleared", "reconciled"] if status == "reconciled" \
                else [status]
            for st in path:
                r = client.patch(f"/api/v1/transactions/{body['id']}",
                                 json={"status": st})
                assert r.status_code == 200, r.text
                body = r.json()
        return body

    def test_list_and_rename(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        self._mk(client, aid, "TESCO STORES")
        self._mk(client, aid, "TESCO STORES")
        self._mk(client, aid, "Sainsburys")
        payees = client.get("/api/v1/payees").json()
        by_name = {p["payee"]: p["transaction_count"] for p in payees}
        assert by_name["TESCO STORES"] == 2
        assert by_name["Sainsburys"] == 1
        r = client.post("/api/v1/payees/rename", json={
            "source": "TESCO STORES", "target": "Tesco"})
        assert r.status_code == 200, r.text
        assert r.json() == {"renamed": 2, "skipped": 0}
        payees = client.get("/api/v1/payees").json()
        names = [p["payee"] for p in payees]
        assert "Tesco" in names and "TESCO STORES" not in names

    def test_rename_skips_reconciled(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        self._mk(client, aid, "Old Name")
        self._mk(client, aid, "Old Name", status="reconciled")
        r = client.post("/api/v1/payees/rename", json={
            "source": "Old Name", "target": "New Name"})
        assert r.json() == {"renamed": 1, "skipped": 1}
        remaining = client.get("/api/v1/transactions?q=Old%20Name")
        assert remaining.json()["total"] == 1

    def test_rename_cross_household(self, client: TestClient) -> None:
        _mk_household(client)
        aid = _mk_account(client)
        self._mk(client, aid, "Shared Payee")
        # a second household's same-name payee must not be touched
        other = _mk_household(client)
        oaid = int(client.post("/api/v1/accounts", json={
            "name": "Other", "type": "joint", "opening_balance": 0,
            "opening_date": "2026-01-01"},
            headers={"X-Household-Id": str(other)}).json()["id"])
        r = client.post("/api/v1/transactions", json={
            "account_id": oaid, "date": "2026-05-10",
            "amount": -100, "payee": "Shared Payee"},
            headers={"X-Household-Id": str(other)})
        assert r.status_code == 201, r.text
        r = client.post("/api/v1/payees/rename", json={
            "source": "Shared Payee", "target": "Mine Only"})
        assert r.json()["renamed"] == 1
        theirs = client.get("/api/v1/payees",
                            headers={"X-Household-Id": str(other)})
        assert "Shared Payee" in [p["payee"] for p in theirs.json()]


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
