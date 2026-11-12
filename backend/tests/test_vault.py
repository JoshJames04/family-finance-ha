"""Issue #30 — encrypted credential vault.

ACs: token at rest is ciphertext (raw store read); round-trip works
server-side; a credential can never be serialised to an API
response (schema-level); deleting purges refs.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CredentialRefOut
from app.core.settings import Settings
from app.domain import vault
from app.domain.vault import VaultError
from app.persistence.models import Household, VaultEntry

SECRET = b"starling-pat-abc123"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings.load({"FF_DATA_DIR": str(tmp_path)})


@pytest.fixture()
def ring(settings: Settings) -> vault.Keyring:
    return vault.keyring(settings)


class TestAtRest:
    def test_stored_is_ciphertext_not_plaintext(
            self, session: Session, household: Household,
            ring: vault.Keyring) -> None:
        """AC: raw store read shows ciphertext, not the token."""
        vault.store(session, household.id, "starling:token",
                    SECRET, ring)
        raw = session.scalar(select(VaultEntry))
        assert raw is not None
        assert raw.ciphertext != SECRET
        assert SECRET not in raw.ciphertext
        assert raw.key_version == 1  # file-key generation

    def test_round_trip(self, session: Session, household: Household,
                        ring: vault.Keyring) -> None:
        """AC: store → retrieve returns the original secret."""
        vault.store(session, household.id, "starling:token",
                    SECRET, ring)
        assert vault.retrieve(session, household.id,
                              "starling:token", ring) == SECRET

    def test_update_existing_ref(self, session: Session,
                                 household: Household,
                                 ring: vault.Keyring) -> None:
        vault.store(session, household.id, "r", b"old", ring)
        vault.store(session, household.id, "r", b"new", ring)
        rows = session.scalars(select(VaultEntry)).all()
        assert len(rows) == 1
        assert vault.retrieve(session, household.id, "r",
                              ring) == b"new"


class TestSchemaGuarantee:
    def test_response_schema_cannot_carry_secret(self) -> None:
        """AC: serialising a credential to an API response is
        impossible — the Out model has no field that could hold
        secret material."""
        fields = set(CredentialRefOut.model_fields)
        forbidden = {"secret", "value", "ciphertext", "token",
                     "plaintext", "key", "blob"}
        assert fields.isdisjoint(forbidden)

    def test_ref_out_from_entry_drops_ciphertext(
            self, session: Session, household: Household,
            ring: vault.Keyring) -> None:
        e = vault.store(session, household.id, "r", SECRET, ring)
        out = CredentialRefOut.model_validate(e).model_dump()
        assert SECRET.decode() not in str(out)
        assert not any(isinstance(v, bytes) for v in out.values())


class TestPurge:
    def test_delete_removes_row_and_retrieval_fails(
            self, session: Session, household: Household,
            ring: vault.Keyring) -> None:
        """AC: deleting purges the vault ref."""
        e = vault.store(session, household.id, "r", SECRET, ring)
        vault.delete(session, e)
        assert session.scalar(select(VaultEntry)) is None
        with pytest.raises(VaultError, match="not found"):
            vault.retrieve(session, household.id, "r", ring)


class TestRotation:
    def test_env_key_rotation_reencrypts(
            self, session: Session, household: Household,
            settings: Settings, ring: vault.Keyring,
            tmp_path: Path) -> None:
        """Entries written under the file key (v1) decrypt fine, then
        rotate to the env-provided generation (v2)."""
        e = vault.store(session, household.id, "r", SECRET, ring)
        old_ct, old_v = e.ciphertext, e.key_version
        env_ring = vault.keyring(Settings.load({
            "FF_DATA_DIR": str(tmp_path),
            "FF_VAULT_KEY": base64.urlsafe_b64encode(
                b"k" * 32).decode()}))
        n = vault.rotate(session, env_ring)
        assert n == 1
        assert e.key_version == 2
        assert e.ciphertext != old_ct
        assert vault.retrieve(session, household.id, "r",
                              env_ring) == SECRET
        assert old_v == 1

    def test_env_only_ring_still_reads_file_generation(
            self, session: Session, household: Household,
            tmp_path: Path) -> None:
        s1 = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        vault.store(session, household.id, "r", SECRET,
                    vault.keyring(s1))
        s2 = Settings.load({
            "FF_DATA_DIR": str(tmp_path),
            "FF_VAULT_KEY": base64.urlsafe_b64encode(
                b"k" * 32).decode()})
        assert vault.retrieve(session, household.id, "r",
                              vault.keyring(s2)) == SECRET


class TestApi:
    def test_store_list_purge_round_trip(
            self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from app.main import create_app
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))
        ) as client:
            hid = client.post("/api/v1/households",
                              json={"name": "H"}).json()["id"]
            admin = client.post("/api/v1/profiles", headers={"X-Household-Id": str(hid)},
                                json={"display_name": "Admin", "role": "admin"}).json()["id"]
            h = {"X-Household-Id": str(hid), "X-Profile-Id": str(admin)}
            r = client.post("/api/v1/credentials", json={
                "ref": "starling:token", "secret": "pat-123"},
                headers=h)
            assert r.status_code == 201
            body = r.json()
            assert "pat-123" not in str(body)
            assert set(body) == {"id", "ref", "key_version",
                                 "created_at", "updated_at"}
            items = client.get("/api/v1/credentials",
                               headers=h).json()
            assert len(items) == 1
            assert "pat-123" not in str(items)
            cid = body["id"]
            assert client.delete(
                f"/api/v1/credentials/{cid}", headers=h
            ).status_code == 204
            assert client.get("/api/v1/credentials",
                              headers=h).json() == []
