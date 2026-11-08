"""Issue #65 — bank connection + token lifecycle.

ACs: revoked token → connection marked revoked, sync stops, re-auth
offered; token never leaves the backend (schema test).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import BankConnectionOut
from app.core.settings import Settings
from app.domain import banks, vault
from app.domain.banks import TokenInfo, TokenRejected
from app.persistence.models import (
    BankConnection,
    BankProvider,
    ConnStatus,
    Household,
    VaultEntry,
)

GOOD = frozenset(
    {"account:read", "balance:read", "transaction:read"})


def _validator(scopes: frozenset[str] = GOOD,
               label: str = "Personal"):
    def v(token: str) -> TokenInfo:
        if token == "bad":
            raise banks.TokenInvalid("401")
        if token == "flaky":
            raise banks.ValidatorUnavailable("HTTP 503")
        return TokenInfo(scopes=scopes, label=label)
    return v


@pytest.fixture()
def ring(tmp_path: Path) -> vault.Keyring:
    return vault.keyring(Settings.load(
        {"FF_DATA_DIR": str(tmp_path)}))


@pytest.fixture()
def conn(session: Session, household: Household,
         ring: vault.Keyring) -> BankConnection:
    return banks.connect(session, household.id,
                         BankProvider.starling, "pat-1",
                         _validator(), ring)


class TestConnect:
    def test_valid_token_connects_and_vaults(
            self, session: Session, household: Household,
            conn: BankConnection, ring: vault.Keyring) -> None:
        assert conn.status == ConnStatus.connected
        assert conn.scopes
        e = session.scalar(select(VaultEntry))
        assert e is not None
        assert e.ref == conn.vault_ref
        assert vault.retrieve(session, household.id, conn.vault_ref,
                              ring) == b"pat-1"

    def test_payment_scope_rejected(
            self, session: Session, household: Household,
            ring: vault.Keyring) -> None:
        """ADR-0008: a PAT holding payment powers never enters the
        vault."""
        v = _validator(GOOD | {"pay-local-once:create"})
        with pytest.raises(TokenRejected, match="non-read scopes"):
            banks.connect(session, household.id,
                          BankProvider.starling, "pat-x", v, ring)
        assert session.scalar(select(VaultEntry)) is None
        assert session.scalar(select(BankConnection)) is None

    def test_missing_required_scope_rejected(
            self, session: Session, household: Household,
            ring: vault.Keyring) -> None:
        v = _validator(frozenset({"account:read"}))
        with pytest.raises(TokenRejected, match="lacks required"):
            banks.connect(session, household.id,
                          BankProvider.starling, "pat-x", v, ring)

    def test_extra_read_scopes_accepted(
            self, session: Session, household: Household,
            ring: vault.Keyring) -> None:
        """Extra `*:read` scopes are harmless — PAT creators routinely
        tick more boxes than the app needs."""
        v = _validator(GOOD | {
            "payee:read", "statement-pdf:read", "feed-export-csv:read",
            "confirmation-of-funds:read", "pay-local:read"})
        conn = banks.connect(session, household.id,
                             BankProvider.starling, "pat-x", v, ring)
        assert conn.status == ConnStatus.connected


class TestStateMachine:
    def test_401_revokes_and_sync_stops(
            self, session: Session, conn: BankConnection) -> None:
        """AC: revoked token → revoked status, sync stops cleanly."""
        banks.record_api_error(session, conn, 401, "unauthorised")
        assert conn.status == ConnStatus.revoked
        assert banks.syncable(conn) is False

    def test_429_degrades_but_syncable(
            self, session: Session, conn: BankConnection) -> None:
        banks.record_api_error(session, conn, 429, "rate limited")
        assert conn.status == ConnStatus.degraded
        assert banks.syncable(conn) is True

    def test_revoked_blocks_token_access(
            self, session: Session, conn: BankConnection,
            ring: vault.Keyring) -> None:
        banks.record_api_error(session, conn, 403, "forbidden")
        with pytest.raises(banks.ConnError, match="revoked"):
            banks.token(session, conn, ring)

    def test_reauth_after_revocation(
            self, session: Session, household: Household,
            conn: BankConnection, ring: vault.Keyring) -> None:
        """AC: re-auth path offered — rotate restores the same
        connection id to connected."""
        banks.record_api_error(session, conn, 401, "unauthorised")
        cid = conn.id
        banks.rotate(session, conn, "pat-2", _validator(), ring)
        assert conn.id == cid
        assert conn.status == ConnStatus.connected
        assert vault.retrieve(session, household.id, conn.vault_ref,
                              ring) == b"pat-2"

    def test_disconnect_purges_vault(
            self, session: Session, household: Household,
            conn: BankConnection, ring: vault.Keyring) -> None:
        """AC: disconnect purges the vault ref — no token material
        survives."""
        banks.disconnect(session, conn, ring)
        assert conn.status == ConnStatus.revoked
        assert session.scalar(select(VaultEntry)) is None
        with pytest.raises(vault.VaultError):
            vault.retrieve(session, household.id, conn.vault_ref,
                           ring)


class TestSchema:
    def test_connection_out_cannot_carry_token(self) -> None:
        """AC: token never leaves the backend — the Out model has
        no field able to hold secret material."""
        fields = set(BankConnectionOut.model_fields)
        forbidden = {"token", "secret", "value", "ciphertext",
                     "password"}
        assert fields.isdisjoint(forbidden)


class TestApi:
    def test_connect_list_disconnect(
            self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from app.main import create_app
        app = create_app(Settings.load(
            {"FF_DATA_DIR": str(tmp_path)}))
        app.state.token_validator = _validator()
        with TestClient(app) as client:
            hid = client.post("/api/v1/households",
                              json={"name": "H"}).json()["id"]
            h = {"X-Household-Id": str(hid)}
            r = client.post("/api/v1/banking/connections", json={
                "provider": "starling", "token": "pat-1"},
                headers=h)
            assert r.status_code == 201
            body = r.json()
            assert body["status"] == "connected"
            assert "pat-1" not in str(body)
            cid = body["id"]
            r = client.post("/api/v1/banking/connections", json={
                "provider": "starling", "token": "bad"}, headers=h)
            assert r.status_code == 401
            items = client.get("/api/v1/banking/connections",
                               headers=h).json()
            assert len(items) == 1
            r = client.post(
                f"/api/v1/banking/connections/{cid}/disconnect",
                headers=h)
            assert r.json()["status"] == "revoked"

    def test_multiple_connections_same_provider(
            self, tmp_path: Path) -> None:
        """A second PAT mints a second connection row (personal +
        joint); reconnecting with a dead row present revives it
        instead of minting a duplicate."""
        from fastapi.testclient import TestClient

        from app.main import create_app
        app = create_app(Settings.load(
            {"FF_DATA_DIR": str(tmp_path)}))
        app.state.token_validator = _validator()
        with TestClient(app) as client:
            hid = client.post("/api/v1/households",
                              json={"name": "H"}).json()["id"]
            h = {"X-Household-Id": str(hid)}
            c1 = client.post("/api/v1/banking/connections", json={
                "provider": "starling", "token": "pat-1",
                "label": "Personal"}, headers=h)
            assert c1.status_code == 201
            c2 = client.post("/api/v1/banking/connections", json={
                "provider": "starling", "token": "pat-2",
                "label": "Joint"}, headers=h)
            assert c2.status_code == 201
            cid1, cid2 = c1.json()["id"], c2.json()["id"]
            assert cid2 != cid1
            items = client.get("/api/v1/banking/connections",
                               headers=h).json()
            assert {i["id"] for i in items} == {cid1, cid2}
            assert next(i for i in items if i["id"] == cid2
                        )["label"] == "Joint"
            # Kill the joint link — reconnecting revives that row
            # (id stable, mappings preserved) rather than minting
            # a third connection.
            client.post(
                f"/api/v1/banking/connections/{cid2}/disconnect",
                headers=h)
            c3 = client.post("/api/v1/banking/connections", json={
                "provider": "starling", "token": "pat-3",
                "label": "Joint"}, headers=h)
            assert c3.status_code == 201
            assert c3.json()["id"] == cid2
            assert len(client.get("/api/v1/banking/connections",
                                  headers=h).json()) == 2

    def test_discover_link_joint(
            self, tmp_path: Path) -> None:
        """Discover proposes a space sharing the account's uid; link
        can mint a joint account for it."""
        from fastapi.testclient import TestClient

        from app.domain.discovery import DiscoveredAccount
        from app.main import create_app

        class FakeClient:
            def accounts(self, token: str) -> list[DiscoveredAccount]:
                return [
                    DiscoveredAccount("uid-1", "Personal", "account",
                                      category_uid="cat-main"),
                    DiscoveredAccount("uid-1", "Joint Bills", "space",
                                      balance_pence=40000,
                                      category_uid="cat-space")]
            def balance(self, token: str, uid: str,
                        cat: str | None = None) -> int:
                return 40000
            def feed_items(self, token, uid, cat, since):
                return []

        app = create_app(Settings.load(
            {"FF_DATA_DIR": str(tmp_path)}))
        app.state.token_validator = _validator()
        app.state.bank_client = FakeClient()
        with TestClient(app) as client:
            hid = client.post("/api/v1/households",
                              json={"name": "H"}).json()["id"]
            h = {"X-Household-Id": str(hid)}
            cid = client.post("/api/v1/banking/connections", json={
                "provider": "starling", "token": "pat-1"},
                headers=h).json()["id"]
            maps = client.post(
                f"/api/v1/banking/connections/{cid}/discover",
                headers=h).json()
            assert len(maps) == 2
            space = next(m for m in maps
                         if m["external_kind"] == "space")
            r = client.post(
                f"/api/v1/banking/mappings/{space['id']}/link",
                json={"create_account": True, "account_type": "joint"},
                headers=h)
            assert r.status_code == 200, r.text
            acct_id = r.json()["account_id"]
            accts = client.get("/api/v1/accounts", headers=h).json()
            mine = next(a for a in accts["items"] if a["id"] == acct_id)
            assert mine["type"] == "joint"
            assert mine["name"] == "Joint Bills"
