"""Standalone provider observations share transaction-sync ownership (#522)."""
import threading
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1 import banks as bankapi
from app.core.settings import Settings
from app.domain import banks, discovery, vault
from app.domain.sync_lock import claim
from app.main import create_app
from app.persistence.models import (
    BankAccountMapping,
    BankConnection,
    ConnStatus,
    Household,
    IntegrationEvent,
    VaultEntry,
)
from tests.test_banking_live142 import linked


class Provider:
    def accounts(self, *args):
        return [discovery.DiscoveredAccount("1", "Account 1", category_uid="category-1",
                                            balance_pence=2000)]

    def balance(self, *args):
        return 2000


@pytest.fixture()
def observation(tmp_path, monkeypatch):
    app = create_app(Settings.load({"FF_DATA_DIR": str(tmp_path),
        "FF_SCHEDULER_TICK_SECONDS": "0", "FF_BACKUP_INTERVAL_HOURS": "0"}))
    monkeypatch.setattr(bankapi.banks, "token", lambda *_: "synthetic")
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            household = Household(name="Synthetic observation")
            session.add(household)
            session.flush()
            _, conn, mapping = linked(session, household)
            mapping.balance_pence = 1000
            ids = household.id, conn.id, mapping.id
            session.commit()
        yield client, ids


@pytest.mark.parametrize("endpoint", ["discover", "sync-balances", "rotate", "disconnect"])
def test_standalone_observation_does_not_bypass_existing_connection_owner(observation, endpoint):
    client, (hid, cid, _) = observation
    class NoProvider:
        def accounts(self, *args):
            raise AssertionError("overlapping discovery reached provider")
        def balance(self, *args):
            raise AssertionError("overlapping balance refresh reached provider")
    client.app.state.bank_client = NoProvider()
    def no_validator(_):
        raise AssertionError("overlapping rotation validated credentials")
    client.app.state.token_validator = no_validator
    with Session(client.app.state.engine) as owner, claim(owner, f"connection:{cid}") as acquired:
        assert acquired
        response = client.post(f"/api/v1/banking/connections/{cid}/{endpoint}",
                               headers={"X-Household-Id": str(hid)},
                               **({"json": {"provider": "starling", "token": "replacement"}}
                                  if endpoint == "rotate" else {}))
    assert response.status_code == 409
    assert "retry" in response.json()["title"]


@pytest.mark.parametrize("endpoint", ["discover", "sync-balances", "rotate", "disconnect"])
def test_observation_commits_before_releasing_owner_and_identical_refresh_stays_quiet(
        observation, monkeypatch, endpoint):
    client, (hid, cid, mid) = observation
    client.app.state.bank_client = Provider()
    client.app.state.token_validator = lambda _: banks.TokenInfo(banks.REQUIRED_SCOPES)
    releases = []
    @contextmanager
    def audited_claim(session, scope):
        with claim(session, scope) as acquired:
            yield acquired
            if acquired:
                with Session(client.app.state.engine) as persisted:
                    if endpoint in ("discover", "sync-balances"):
                        assert persisted.get(BankAccountMapping, mid).balance_pence == 2000
                        assert len(list(persisted.scalars(select(IntegrationEvent).where(
                            IntegrationEvent.event_type == "account.balance_changed")))) == 1
                    elif endpoint == "rotate":
                        assert persisted.get(BankConnection, cid).status == ConnStatus.connected
                        assert persisted.scalar(select(VaultEntry)) is not None
                    else:
                        assert persisted.get(BankConnection, cid).status == ConnStatus.revoked
                        assert persisted.scalar(select(VaultEntry)) is None
                releases.append(scope)
    monkeypatch.setattr(bankapi, "claim", audited_claim)
    for _ in range(2):
        response = client.post(f"/api/v1/banking/connections/{cid}/{endpoint}",
                               headers={"X-Household-Id": str(hid)},
                               **({"json": {"provider": "starling", "token": "replacement"}}
                                  if endpoint == "rotate" else {}))
        assert response.status_code == 200, response.text
    assert releases == [f"connection:{cid}", f"connection:{cid}"]


def test_successful_inflight_sync_cannot_overwrite_concurrent_disconnect(observation):
    from tests.test_bank_sync_490 import manual
    client, (hid, cid, _) = observation
    ring = vault.keyring(client.app.state.settings)
    with Session(client.app.state.engine) as session:
        conn = session.get(BankConnection, cid)
        vault.store(session, hid, conn.vault_ref, b"original-synthetic", ring)
        session.commit()
    entered, release = threading.Event(), threading.Event()
    errors = []
    class Blocking(Provider):
        def feed_items(self, *args):
            entered.set()
            assert release.wait(15)
            return []
    def owner():
        try:
            with Session(client.app.state.engine) as session:
                manual(session, hid, cid, Blocking())
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=owner)
    thread.start()
    assert entered.wait(10)
    try:
        response = client.post(f"/api/v1/banking/connections/{cid}/disconnect",
                               headers={"X-Household-Id": str(hid)})
    finally:
        release.set()
        thread.join(15)
    assert not thread.is_alive() and errors == []
    with Session(client.app.state.engine) as persisted:
        conn = persisted.get(BankConnection, cid)
        assert response.status_code == 409, f"accepted removal then sync ended {conn.status}"
        assert conn.status == ConnStatus.connected
        assert vault.retrieve(persisted, hid, conn.vault_ref, ring) == b"original-synthetic"


def test_unrelated_connection_can_change_while_another_connection_is_owned(observation):
    client, (hid, cid, _) = observation
    with Session(client.app.state.engine) as session:
        _, other, _ = linked(session, session.get(Household, hid), suffix="2")
        other_id = other.id
        session.commit()
    with (Session(client.app.state.engine) as session,
          claim(session, f"connection:{cid}") as acquired):
        assert acquired
        response = client.post(f"/api/v1/banking/connections/{other_id}/disconnect",
                               headers={"X-Household-Id": str(hid)})
    assert response.status_code == 200
    with Session(client.app.state.engine) as persisted:
        assert persisted.get(BankConnection, cid).status == ConnStatus.connected
        assert persisted.get(BankConnection, other_id).status == ConnStatus.revoked


def test_connect_reauthentication_cannot_bypass_existing_connection_owner(observation):
    client, (hid, cid, _) = observation
    with Session(client.app.state.engine) as session:
        session.get(BankConnection, cid).status = ConnStatus.revoked
        session.commit()
    def no_validator(_):
        raise AssertionError("overlapping connect validated credentials")
    client.app.state.token_validator = no_validator
    with (Session(client.app.state.engine) as session,
          claim(session, f"connection:{cid}") as acquired):
        assert acquired
        response = client.post("/api/v1/banking/connections",
                               headers={"X-Household-Id": str(hid)},
                               json={"provider": "starling", "token": "replacement"})
    assert response.status_code == 409


def test_connect_reauthentication_commits_under_owner_but_new_connections_are_independent(
        observation, monkeypatch):
    client, (hid, cid, _) = observation
    with Session(client.app.state.engine) as session:
        session.get(BankConnection, cid).status = ConnStatus.revoked
        session.commit()
    client.app.state.token_validator = lambda _: banks.TokenInfo(banks.REQUIRED_SCOPES)
    releases = []
    @contextmanager
    def audited_claim(session, scope):
        with claim(session, scope) as acquired:
            yield acquired
            with Session(client.app.state.engine) as persisted:
                assert persisted.get(BankConnection, cid).status == ConnStatus.connected
                assert persisted.scalar(select(VaultEntry)) is not None
            releases.append(scope)
    monkeypatch.setattr(bankapi, "claim", audited_claim)
    body = {"provider": "starling", "token": "replacement"}
    response = client.post("/api/v1/banking/connections", headers={"X-Household-Id": str(hid)},
                           json=body)
    assert response.status_code == 201 and response.json()["id"] == cid
    assert releases == [f"connection:{cid}"]
    with (Session(client.app.state.engine) as session,
          claim(session, f"connection:{cid}") as acquired):
        assert acquired
        another = client.post("/api/v1/banking/connections",
                              headers={"X-Household-Id": str(hid)}, json=body)
    assert another.status_code == 201 and another.json()["id"] != cid
