"""Issue #115 — CSV export fidelity + member scoping.

ACs: a period export produces complete, correctly formatted rows;
private-scoped data exports only for its owner.
"""
from __future__ import annotations

import csv
import io
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


def _household(client: TestClient) -> dict[str, str]:
    hid = client.post("/api/v1/households",
                      json={"name": "H"}).json()["id"]
    return {"X-Household-Id": str(hid)}


def _account(client: TestClient, h: dict[str, str], name: str,
             owner_profile_id: int | None = None) -> int:
    body: dict[str, object] = {
        "name": name, "type": "personal",
        "opening_balance": 0, "opening_date": "2024-12-31"}
    if owner_profile_id is not None:
        body["owner_profile_id"] = owner_profile_id
    return client.post("/api/v1/accounts", json=body,
                       headers=h).json()["id"]


def _txn(client: TestClient, h: dict[str, str], account_id: int,
         on: str, pence: int, payee: str = "shop",
         notes: str | None = None) -> int:
    body: dict[str, object] = {
        "account_id": account_id, "date": on,
        "amount": pence, "payee": payee}
    if notes is not None:
        body["notes"] = notes
    r = client.post("/api/v1/transactions", json=body, headers=h)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def _export(client: TestClient, h: dict[str, str],
            extra: dict[str, str] | None = None) -> list[dict[str, str]]:
    headers = dict(h)
    if extra:
        headers.update(extra)
    r = client.get("/api/v1/transactions/export.csv",
                   params={"date_from": "2025-01-01",
                           "date_to": "2025-01-31"},
                   headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "transactions_2025-01-01_2025-01-31.csv" in \
        r.headers["content-disposition"]
    return list(csv.DictReader(io.StringIO(r.text)))


def _member(client: TestClient, h: dict[str, str],
            name: str, role: str = "member") -> int:
    r = client.post("/api/v1/profiles",
                    json={"display_name": name, "role": role},
                    headers=h)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


class TestFidelity:
    def test_complete_formatted_rows(self, client: TestClient) -> None:
        h = _household(client)
        aid = _account(client, h, "Current")
        _txn(client, h, aid, "2025-01-05", -1_234, "Tesco")
        _txn(client, h, aid, "2025-01-10", 50_000, "Wages",
             notes='quoted, "comma" note')
        outside = _txn(client, h, aid, "2025-02-01", -9_999)
        rows = _export(client, h)
        assert len(rows) == 2          # out-of-period row excluded
        assert {r["payee"] for r in rows} == {"Tesco", "Wages"}
        tesco = next(r for r in rows if r["payee"] == "Tesco")
        assert tesco["amount_pence"] == "-1234"
        assert tesco["account"] == "Current"
        assert tesco["date"] == "2025-01-05"
        assert tesco["status"] == "pending"
        wages = next(r for r in rows if r["payee"] == "Wages")
        # csv module must round-trip embedded comma+quote notes.
        assert wages["notes"] == 'quoted, "comma" note'
        _ = outside

    def test_deterministic_order(self, client: TestClient) -> None:
        h = _household(client)
        aid = _account(client, h, "Current")
        _txn(client, h, aid, "2025-01-10", -1)
        _txn(client, h, aid, "2025-01-05", -2)
        _txn(client, h, aid, "2025-01-05", -3)
        rows = _export(client, h)
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates)
        # Same-date rows tie-break by id — stable across runs.
        again = _export(client, h)
        assert [r["id"] for r in rows] == [r["id"] for r in again]

    def test_date_window_guard(self, client: TestClient) -> None:
        h = _household(client)
        r = client.get("/api/v1/transactions/export.csv",
                       params={"date_from": "2025-02-01",
                               "date_to": "2025-01-01"},
                       headers=h)
        assert r.status_code == 422


class TestScope:
    def test_private_account_exports_owner_only(
            self, client: TestClient) -> None:
        """AC: private-scoped data exports only for its owner."""
        h = _household(client)
        ada = _member(client, h, "Ada")
        shared_aid = _account(client, h, "Joint")
        ada_aid = _account(client, h, "Ada Private",
                           owner_profile_id=ada)
        _txn(client, h, shared_aid, "2025-01-05", -100, "SharedShop")
        # Private-account writes need the owner's identity now (#32).
        _txn(client, {**h, "X-Profile-Id": str(ada)}, ada_aid,
             "2025-01-06", -200, "AdaShop")

        anonymous = _export(client, h)
        assert {r["payee"] for r in anonymous} == {"SharedShop"}

        stranger = _member(client, h, "Bob")
        bob_view = _export(
            client, h, {"X-Profile-Id": str(stranger)})
        assert {r["payee"] for r in bob_view} == {"SharedShop"}

        ada_view = _export(
            client, h, {"X-Profile-Id": str(ada)})
        assert {r["payee"] for r in ada_view} == {
            "SharedShop", "AdaShop"}

    def test_admin_sees_all(self, client: TestClient) -> None:
        h = _household(client)
        ada = _member(client, h, "Ada")
        ada_aid = _account(client, h, "Ada Private",
                           owner_profile_id=ada)
        _txn(client, {**h, "X-Profile-Id": str(ada)}, ada_aid,
             "2025-01-06", -200, "AdaShop")
        admin = _member(client, h, "Root", role="admin")
        view = _export(client, h, {"X-Profile-Id": str(admin)})
        assert {r["payee"] for r in view} == {"AdaShop"}
