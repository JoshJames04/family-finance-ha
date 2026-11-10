"""Issue #422 — opt-in Starling V2 webhook receiver.

ACs: route exists only when FF_STARLING_WEBHOOK_PUBLIC_KEY is
configured; X-Hook-Signature verified SHA512withRSA over the raw
body (constant-time by the crypto lib); verified deliveries queue
a cursor refresh through the normal sync pipeline — never a
ledger write; polling stays enabled.
"""
from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.settings import Settings
from app.domain import sync as syncdom
from app.main import create_app
from app.persistence.engine import create_app_engine
from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    BankProvider,
    ConnStatus,
    Household,
    MappingStatus,
    TransactionSource,
)


@pytest.fixture()
def keypair():
    priv = rsa.generate_private_key(public_exponent=65537,
                                    key_size=2048)
    pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pem


def _sign(priv, body: bytes) -> str:
    return base64.b64encode(priv.sign(
        body, padding.PKCS1v15(), hashes.SHA512())).decode()


def _app(tmp_path: Path, public_key: str | None):
    env = {"FF_DATA_DIR": str(tmp_path)}
    if public_key is not None:
        env["FF_STARLING_WEBHOOK_PUBLIC_KEY"] = public_key
    settings = Settings.load(env)
    return create_app(settings), settings


def _seed(tmp_path: Path, settings: Settings, *, status=MappingStatus.linked):
    """A linked mapping the webhook should resolve to a refresh."""
    factory = sessionmaker(bind=create_app_engine(
        settings.resolved_database_url))
    with factory() as s:
        hh = Household(name="H")
        s.add(hh)
        s.flush()
        acct = Account(household_id=hh.id, name="Current",
                       type="personal", opening_balance_pence=0,
                       opening_date=date(2025, 1, 1))
        s.add(acct)
        s.flush()
        conn = BankConnection(household_id=hh.id,
                              provider=BankProvider.starling,
                              vault_ref="v1", status=ConnStatus.connected)
        s.add(conn)
        s.flush()
        m = BankAccountMapping(
            household_id=hh.id, connection_id=conn.id,
            external_uid="ext-1", external_label="Current",
            external_category_uid="cat-1",
            status=status, account_id=acct.id)
        s.add(m)
        s.commit()
        return s, acct.id


def _session(tmp_path: Path, settings: Settings):
    factory = sessionmaker(bind=create_app_engine(
        settings.resolved_database_url))
    return factory()


class TestRouteGating:
    def test_unconfigured_app_has_no_route(self, tmp_path):
        app, _ = _app(tmp_path, None)
        with TestClient(app) as c:
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=b"{}", headers={
                           "X-Hook-Signature": "x"})
        # SPA fallback is GET-only, so an unregistered path answers
        # 405 rather than 404 — either way no POST handler exists.
        assert r.status_code in (404, 405)

    def test_configured_app_registers_route(self, tmp_path, keypair):
        priv, pem = keypair
        app, _ = _app(tmp_path, pem)
        with TestClient(app) as c:
            body = json.dumps({"accountUid": "unknown"}).encode()
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=body, headers={
                           "X-Hook-Signature": _sign(priv, body)})
        assert r.status_code == 200
        assert r.json()["refreshes_queued"] == 0


class TestSignatureVerification:
    def test_valid_signature_accepted(self, tmp_path, keypair):
        priv, pem = keypair
        app, settings = _app(tmp_path, pem)
        with TestClient(app) as c:
            _, account_id = _seed(tmp_path, settings)
            body = json.dumps({
                "webhookUid": "wh-1",
                "webhookType": "FEED_ITEM",
                "accountUid": "ext-1",
                "content": {"feedItemUid": "fi-1",
                            "categoryUid": "cat-1"},
            }).encode()
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=body, headers={
                           "X-Hook-Signature": _sign(priv, body)})
        assert r.status_code == 200
        assert r.json() == {"status": "accepted",
                            "refreshes_queued": 1}
        with _session(tmp_path, settings) as s:
            cursor = syncdom.cursor_for(
                s, account_id, TransactionSource.starling)
            assert cursor.refresh_requested is True

    def test_bad_signature_rejected(self, tmp_path, keypair):
        priv, pem = keypair
        other = rsa.generate_private_key(public_exponent=65537,
                                         key_size=2048)
        app, _ = _app(tmp_path, pem)
        body = b'{"accountUid": "ext-1"}'
        with TestClient(app) as c:
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=body, headers={
                           "X-Hook-Signature": _sign(other, body)})
        assert r.status_code == 401

    def test_missing_signature_rejected(self, tmp_path, keypair):
        _, pem = keypair
        app, _ = _app(tmp_path, pem)
        with TestClient(app) as c:
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=b'{"accountUid": "ext-1"}')
        assert r.status_code == 401

    def test_garbage_signature_rejected(self, tmp_path, keypair):
        _, pem = keypair
        app, _ = _app(tmp_path, pem)
        with TestClient(app) as c:
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=b"{}", headers={
                           "X-Hook-Signature": "not-base64!!!"})
        assert r.status_code == 401

    def test_bare_key_body_accepted(self, tmp_path, keypair):
        """Starling supplies the key as a bare base64 body too."""
        priv, pem = keypair
        bare = "".join(
            line for line in pem.splitlines()
            if "PUBLIC KEY" not in line)
        app, _ = _app(tmp_path, bare)
        body = b'{"accountUid": "unknown"}'
        with TestClient(app) as c:
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=body, headers={
                           "X-Hook-Signature": _sign(priv, body)})
        assert r.status_code == 200


class TestRefreshQueueing:
    def test_tampered_body_never_queues(self, tmp_path, keypair):
        priv, pem = keypair
        app, settings = _app(tmp_path, pem)
        with TestClient(app) as c:
            _, account_id = _seed(tmp_path, settings)
            body = b'{"accountUid": "ext-1"}'
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=body + b" ", headers={
                           "X-Hook-Signature": _sign(priv, body)})
        assert r.status_code == 401
        with _session(tmp_path, settings) as s:
            cursor = syncdom.cursor_for(
                s, account_id, TransactionSource.starling)
            assert cursor.refresh_requested is False

    def test_unlinked_mapping_not_refreshed(self, tmp_path, keypair):
        priv, pem = keypair
        app, settings = _app(tmp_path, pem,
                             )
        with TestClient(app) as c:
            _, account_id = _seed(tmp_path, settings,
                                  status=MappingStatus.proposed)
            body = json.dumps({"accountUid": "ext-1",
                               "content": {"categoryUid": "cat-1"}}
                              ).encode()
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=body, headers={
                           "X-Hook-Signature": _sign(priv, body)})
        assert r.status_code == 200
        assert r.json()["refreshes_queued"] == 0
        with _session(tmp_path, settings) as s:
            cursor = syncdom.cursor_for(
                s, account_id, TransactionSource.starling)
            assert cursor.refresh_requested is False

    def test_top_level_fields_resolve(self, tmp_path, keypair):
        """Some event types put the uids top-level, not nested."""
        priv, pem = keypair
        app, settings = _app(tmp_path, pem)
        with TestClient(app) as c:
            _seed(tmp_path, settings)
            body = json.dumps({
                "webhookType": "STANDING_ORDER_PAYMENT",
                "accountUid": "ext-1", "categoryUid": "cat-1",
            }).encode()
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=body, headers={
                           "X-Hook-Signature": _sign(priv, body)})
        assert r.status_code == 200
        assert r.json()["refreshes_queued"] == 1

    def test_non_json_body_with_valid_signature_400(
            self, tmp_path, keypair):
        priv, pem = keypair
        app, _ = _app(tmp_path, pem)
        body = b"not-json"
        with TestClient(app) as c:
            r = c.post("/api/v1/integrations/starling/webhook",
                       content=body, headers={
                           "X-Hook-Signature": _sign(priv, body)})
        assert r.status_code == 400
