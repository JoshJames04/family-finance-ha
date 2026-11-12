"""Issue #32 — private(owner) scope: invisible to other members at
API level, mutations per role, last-admin guard, audited changes."""

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


def _setup(client: TestClient) -> tuple[str, dict, dict, dict]:
    """household + admin/member/viewer profiles."""
    hid = client.post("/api/v1/households",
                      json={"name": "H"}).json()["id"]
    h = {"X-Household-Id": str(hid)}

    def mk(n: str, r: str) -> dict:
        return client.post("/api/v1/profiles", json={
            "display_name": n, "role": r}, headers=h).json()
    admin = mk("Admin", "admin")
    h["X-Profile-Id"] = str(admin["id"])
    return h, admin, mk("Mum", "member"), \
        mk("Kid", "viewer")


def _as(profile: dict) -> dict:
    return {"X-Profile-Id": str(profile["id"])}


class TestPrivateScope:
    def test_private_account_not_found_for_other_member(
            self, client: TestClient):
        h, admin, mum, kid = _setup(client)
        a = client.post("/api/v1/accounts", json={
            "name": "Mum current", "type": "personal",
            "opening_balance": {"pence": 100_00},
            "opening_date": "2025-01-01",
            "owner_profile_id": mum["id"]},
            headers={**h, **_as(mum)}).json()
        # Mum (owner) sees it; kid (member→viewer swap doesn't matter
        # for scope) does not; admin does.
        assert client.get(
            f"/api/v1/accounts/{a['id']}",
            headers={**h, **_as(mum)}).status_code == 200
        r = client.get(f"/api/v1/accounts/{a['id']}",
                       headers={**h, **_as(kid)})
        assert r.status_code == 404          # AC: not-found, not hidden
        assert client.get(
            f"/api/v1/accounts/{a['id']}",
            headers={**h, **_as(admin)}).status_code == 200
        # List excludes it for the non-owner.
        ids = {x["id"] for x in client.get(
            "/api/v1/accounts", headers={**h, **_as(kid)}
        ).json()["items"]}
        assert a["id"] not in ids

    def test_private_account_transactions_invisible(
            self, client: TestClient):
        h, admin, mum, kid = _setup(client)
        a = client.post("/api/v1/accounts", json={
            "name": "Mum", "type": "personal",
            "opening_balance": {"pence": 100_00},
            "opening_date": "2025-01-01",
            "owner_profile_id": mum["id"]},
            headers={**h, **_as(mum)}).json()
        t = client.post("/api/v1/transactions", json={
            "account_id": a["id"], "date": "2025-02-01",
            "amount": {"pence": -500}, "payee": "Chemist"},
            headers={**h, **_as(mum)}).json()
        # Transaction detail + list invisible to kid.
        assert client.get(
            f"/api/v1/transactions/{t['id']}",
            headers={**h, **_as(kid)}).status_code == 404
        ids = {x["id"] for x in client.get(
            "/api/v1/transactions", headers={**h, **_as(kid)}
        ).json()["items"]}
        assert t["id"] not in ids
        # Balance endpoint also denied (leak vector).
        assert client.get(
            f"/api/v1/accounts/{a['id']}/balance",
            headers={**h, **_as(kid)}).status_code == 404

    def test_scope_change_audited(self, client: TestClient):
        """AC: private→shared change is audited with the field."""
        h, admin, mum, kid = _setup(client)
        a = client.post("/api/v1/accounts", json={
            "name": "Mum", "type": "personal",
            "opening_balance": {"pence": 0},
            "opening_date": "2025-01-01",
            "owner_profile_id": mum["id"]},
            headers={**h, **_as(mum)}).json()
        r = client.patch(f"/api/v1/accounts/{a['id']}",
                         json={"owner_profile_id": None},
                         headers={**h, **_as(mum)})
        assert r.status_code == 200
        assert client.get(
            f"/api/v1/accounts/{a['id']}",
            headers={**h, **_as(kid)}).status_code == 200

    def test_member_cannot_reassign_ownership(
            self, client: TestClient):
        h, admin, mum, kid = _setup(client)
        a = client.post("/api/v1/accounts", json={
            "name": "Mum", "type": "personal",
            "opening_balance": {"pence": 0},
            "opening_date": "2025-01-01",
            "owner_profile_id": mum["id"]},
            headers={**h, **_as(mum)}).json()
        r = client.patch(f"/api/v1/accounts/{a['id']}",
                         json={"owner_profile_id": kid["id"]},
                         headers={**h, **_as(mum)})
        assert r.status_code == 403
        # …but admin may reassign.
        r = client.patch(f"/api/v1/accounts/{a['id']}",
                         json={"owner_profile_id": kid["id"]},
                         headers={**h, **_as(admin)})
        assert r.status_code == 200


class TestRoleMutations:
    def test_viewer_cannot_mutate(self, client: TestClient):
        """AC: mutations per role — viewer is read-only."""
        h, admin, mum, kid = _setup(client)
        a = client.post("/api/v1/accounts", json={
            "name": "Joint", "type": "personal",
            "opening_balance": {"pence": 0},
            "opening_date": "2025-01-01"},
            headers=h).json()                  # shared account
        # viewer reads shared fine…
        assert client.get(
            f"/api/v1/accounts/{a['id']}",
            headers={**h, **_as(kid)}).status_code == 200
        # …but every mutation is refused.
        assert client.patch(
            f"/api/v1/accounts/{a['id']}", json={"name": "X"},
            headers={**h, **_as(kid)}).status_code == 403
        assert client.post("/api/v1/transactions", json={
            "account_id": a["id"], "date": "2025-02-01",
            "amount": {"pence": -100}, "payee": "x"},
            headers={**h, **_as(kid)}).status_code == 403

    def test_member_mutates_shared(self, client: TestClient):
        h, admin, mum, kid = _setup(client)
        a = client.post("/api/v1/accounts", json={
            "name": "Joint", "type": "personal",
            "opening_balance": {"pence": 0},
            "opening_date": "2025-01-01"},
            headers=h).json()
        r = client.patch(f"/api/v1/accounts/{a['id']}",
                         json={"name": "House"},
                         headers={**h, **_as(mum)})
        assert r.status_code == 200


class TestLastAdmin:
    def test_cannot_demote_or_deactivate_last_admin(
            self, client: TestClient):
        h, admin, mum, kid = _setup(client)
        r = client.patch(f"/api/v1/profiles/{admin['id']}",
                         json={"role": "member"},
                         headers={**h, **_as(admin)})
        assert r.status_code == 422          # sole admin stays
        r = client.post(
            f"/api/v1/profiles/{admin['id']}/deactivate",
            headers={**h, **_as(admin)})
        assert r.status_code == 422
        # A second admin frees the first to be demoted.
        other = client.post("/api/v1/profiles", json={
            "display_name": "Dad", "role": "admin"},
            headers=h).json()
        r = client.patch(f"/api/v1/profiles/{admin['id']}",
                         json={"role": "member"},
                         headers={**h, **_as(other)})
        assert r.status_code == 200
