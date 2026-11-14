"""Synthetic regressions for retry windows and provider failure isolation."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.v1 import banks as bankapi
from app.api.v1.settings import _next_due, _status
from app.core import scheduler
from app.core.settings import Settings
from app.domain import discovery, sync
from app.domain.banks import TokenInvalid, ValidatorUnavailable
from app.integrations import starling
from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    BankProvider,
    ConnStatus,
    MappingStatus,
    SyncStatus,
    TransactionSource,
)


def linked(session, household, suffix="1"):
    account = Account(household_id=household.id, name=f"Account {suffix}",
                      type="personal", opening_date=date(2026, 1, 1),
                      opening_balance_pence=0)
    conn = BankConnection(household_id=household.id, provider=BankProvider.starling,
                          vault_ref=f"synthetic-{suffix}", status=ConnStatus.connected)
    session.add_all([account, conn])
    session.flush()
    mapping = BankAccountMapping(
        household_id=household.id, connection_id=conn.id,
        external_uid=suffix, external_category_uid=f"category-{suffix}",
        external_label=account.name, status=MappingStatus.linked, account_id=account.id)
    session.add(mapping)
    session.flush()
    return account, conn, mapping


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("last_success", [None, datetime(2026, 1, 1)])
def test_active_backoff_blocks_scheduled_provider_calls(session, household, queued, last_success):
    account, _, _ = linked(session, household)
    now = datetime.now()
    cursor = sync.cursor_for(session, account.id, TransactionSource.starling)
    cursor.last_success_at = last_success
    cursor.retry_at = now + timedelta(minutes=10)
    cursor.refresh_requested = queued
    session.commit()

    class NoProviderCalls:
        def accounts(self, token):
            pytest.fail("scheduled discovery bypassed active backoff")

    assert scheduler.sync_tick(session, client=NoProviderCalls(),
                               token_for=lambda *_: "synthetic", now=now) == 0
    assert cursor.refresh_requested is queued


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("last_success", [None, datetime(2026, 1, 1)])
def test_next_due_honors_retry_window(queued, last_success):
    now = datetime.now()
    cursor = SimpleNamespace(last_success_at=last_success, refresh_requested=queued,
                             retry_at=now + timedelta(minutes=10))
    assert _next_due(cursor, now, 900) == cursor.retry_at


def test_settings_separates_failed_sync_from_connected_credentials(session, household):
    account, _, _ = linked(session, household)
    cursor = sync.cursor_for(session, account.id, TransactionSource.starling)
    cursor.status = SyncStatus.failed
    cursor.error = "provider network unavailable"
    session.commit()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=Settings())))
    row = _status(request, session, household.id).connections[0]
    assert row.status == "connected"
    assert row.sync_status == "failed"
    assert row.sync_error == "provider network unavailable"


def test_revoked_connection_has_no_scheduled_next_sync(session, household):
    account, conn, _ = linked(session, household)
    sync.cursor_for(session, account.id, TransactionSource.starling)
    conn.status = ConnStatus.revoked
    session.commit()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=Settings())))
    assert _status(request, session, household.id).connections[0].next_due_at is None


@pytest.mark.parametrize("status", [429, 500, 503])
def test_incomplete_spaces_discovery_preserves_linked_mapping(
        session, household, monkeypatch, status):
    _, conn, mapping = linked(session, household)
    mapping.external_kind = "space"
    session.commit()

    def get(token, path):
        if path.endswith("/spaces"):
            raise starling.UpstreamError(status)
        return {"accounts": [{"accountUid": "1", "defaultCategory": "main"}]}

    monkeypatch.setattr(starling, "_get", get)
    with pytest.raises(starling.UpstreamError):
        discovery.discover(session, conn, starling.StarlingClient(), "synthetic")
    assert mapping.status is MappingStatus.linked


@pytest.mark.parametrize("failure", [ValidatorUnavailable("synthetic DNS failure"),
                                     TokenInvalid("HTTP 401")])
def test_expected_feed_failure_does_not_abort_other_connections(
        session, household, failure):
    a1, c1, _ = linked(session, household, "1")
    a2, _, _ = linked(session, household, "2")

    class Client:
        def accounts(self, token):
            return [discovery.DiscoveredAccount(token, f"Account {token}",
                                               category_uid=f"category-{token}")]

        def balance(self, *args):
            return 0

        def feed_items(self, token, *args):
            if token == "1":
                raise failure
            return []

    assert scheduler.sync_tick(session, client=Client(),
                               token_for=lambda _, conn: "1" if conn.id == c1.id else "2") == 2
    failed = sync.cursor_for(session, a1.id, TransactionSource.starling)
    assert failed.status is SyncStatus.failed
    assert failed.error != "unexpected sync failure"
    healthy = sync.cursor_for(session, a2.id, TransactionSource.starling)
    assert healthy.status is SyncStatus.completed
    assert c1.status is (ConnStatus.revoked if isinstance(failure, TokenInvalid)
                          else ConnStatus.degraded)


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("manual", [False, True])
def test_backoff_prevents_connection_refresh_calls(
        session, household, monkeypatch, mixed, manual):
    account, conn, _ = linked(session, household)
    cursor = sync.cursor_for(session, account.id, TransactionSource.starling)
    cursor.retry_at = datetime.now() + timedelta(minutes=10)
    if mixed:
        _, _, mapping = linked(session, household, "2")
        mapping.connection_id = conn.id
    session.commit()
    monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")
    feeds = []

    class Client:
        def accounts(self, *args):
            pytest.fail("discovery bypassed backoff")

        def balance(self, *args):
            pytest.fail("balances bypassed backoff")

        def feed_items(self, token, uid, *args):
            feeds.append(uid)
            return []

    if manual:
        result = bankapi.sync_transactions(
            conn.id, household, session, "synthetic", None, Client(), SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(
                    settings=Settings(), scheduler_enabled=True))))
        assert result["balances_refreshed"] == 0
        assert [r["status"] for r in result["runs"]] == (
            ["queued", "completed"] if mixed else ["queued"])
    else:
        count = scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic")
        assert count == int(mixed)
    assert feeds == (["2"] if mixed else [])
    assert cursor.refresh_requested is manual


@pytest.mark.parametrize("manual", [False, True])
def test_complete_sync_recovers_connection_error(session, household, monkeypatch, manual):
    _, conn, _ = linked(session, household)
    conn.status = ConnStatus.degraded
    conn.last_error = "old provider failure"
    session.commit()

    class Client:
        def accounts(self, *args):
            return [discovery.DiscoveredAccount("1", "Account 1", category_uid="category-1")]

        def balance(self, *args):
            return 0

        def feed_items(self, *args):
            return []

    if manual:
        monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")
        bankapi.sync_transactions(
            conn.id, household, session, "synthetic", None, Client(), SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(
                    settings=Settings(), scheduler_enabled=True))))
    else:
        scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic")
    assert conn.status is ConnStatus.connected
    assert conn.last_error is None
