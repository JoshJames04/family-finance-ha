"""Issue #375 — cross-install restore + vault re-auth, end-to-end.

Restoring a snapshot onto a second install means the vault ciphertext
decrypts under the *other* install's ``.vault_key``. The sequence must
surface as a re-auth prompt (422), never a 500, and rotating the token
restores sync — the exact path that broke during the 1.0.5 migration.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.domain.banks import TokenInfo
from app.domain.discovery import DiscoveredAccount
from app.main import create_app

ADMIN = {"X-Admin-Token": "s3cret"}


def _settings(tmp_path: Path) -> Settings:
    return Settings.load(
        {"FF_DATA_DIR": str(tmp_path), "FF_ADMIN_TOKEN": "s3cret"})


def _validator(t: str) -> TokenInfo:
    return TokenInfo(scopes=frozenset(
        {"account:read", "balance:read", "transaction:read"}))


class _FakeClient:
    def accounts(self, token: str) -> list[DiscoveredAccount]:
        return [DiscoveredAccount("uid-1", "Personal")]

    def balance(self, token: str, external_uid: str,
                category_uid: str | None = None) -> int:
        return 12345

    def feed_items(self, token: str, external_uid: str,
                   category_uid: str, since: str) -> list:
        return []


class TestCrossInstallRestore:
    def test_restore_then_reauth_then_sync(self, tmp_path: Path) -> None:
        src = _settings(tmp_path / "src")
        dst = _settings(tmp_path / "dst")
        # --- install A: household + bank connection + snapshot ------
        app_a = create_app(src)
        app_a.state.token_validator = _validator
        app_a.state.bank_client = _FakeClient()
        with TestClient(app_a) as a:
            hid = a.post("/api/v1/households",
                         json={"name": "H"}).json()["id"]
            h = {"X-Household-Id": str(hid), **ADMIN}
            conn = a.post("/api/v1/banking/connections", json={
                "provider": "starling", "token": "pat-1"},
                headers=h)
            assert conn.status_code == 201
            snap = a.post("/api/v1/admin/backups", headers=h).json()
            raw = a.get(
                f"/api/v1/admin/backups/{snap['name']}/download",
                headers=h).content

        # --- install B: restore the snapshot at boot ---------------
        app_b = create_app(dst)
        app_b.state.token_validator = _validator
        app_b.state.bank_client = _FakeClient()
        with TestClient(app_b) as b:
            # Upload needs a household to authorize against; the
            # restore replaces it wholesale.
            hid = b.post("/api/v1/households",
                         json={"name": "tmp"}).json()["id"]
            h = {"X-Household-Id": str(hid), **ADMIN}
            up = b.post("/api/v1/admin/restore/upload", headers=h,
                        files={"file": ("snap.db", raw)})
            assert up.status_code == 202
        with TestClient(create_app(dst)):
            pass  # boot applies the pending restore

        app_b2 = create_app(dst)
        app_b2.state.token_validator = _validator
        app_b2.state.bank_client = _FakeClient()
        with TestClient(app_b2) as b:
            hid = b.get("/api/v1/households", headers=ADMIN
                        ).json()["items"][0]["id"]
            h = {"X-Household-Id": str(hid), **ADMIN}
            conns = b.get("/api/v1/banking/connections", headers=h
                          ).json()
            assert len(conns) == 1
            cid = conns[0]["id"]
            # undecryptable token → 422 re-authenticate, never 500
            r = b.post(f"/api/v1/banking/connections/{cid}/sync",
                       headers=h)
            assert r.status_code == 422
            assert "re-authenticate" in r.text
            assert conns[0]["status"] == "connected"
            # rotate the same token → re-encrypted under B's key
            r = b.post(f"/api/v1/banking/connections/{cid}/rotate",
                       headers=h,
                       json={"provider": "starling", "token": "pat-1"})
            assert r.status_code == 200
            assert r.json()["status"] == "connected"
            # sync works again end-to-end
            r = b.post(f"/api/v1/banking/connections/{cid}/sync",
                       headers=h)
            assert r.status_code == 200
