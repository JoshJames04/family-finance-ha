"""Connection-wide provider failures must not fan out into more requests."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1 import banks as bankapi
from app.api.v1.settings import _status
from app.core import scheduler
from app.core.settings import Settings
from app.domain import appconfig, banks, discovery, sync, vault
from app.domain.banks import TokenInvalid
from app.integrations import starling
from app.integrations.starling import UpstreamError
from app.persistence.models import ConnStatus, SyncStatus, TransactionSource
from tests.test_banking_live142 import linked


def request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=Settings(sync_interval_minutes=30))))


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("phase", ["discovery", "balance"])
@pytest.mark.parametrize("status", [401, 429])
def test_metadata_failure_persists_connection_state_without_feed_fanout(
        session, household, monkeypatch, manual, phase, status):
    account, conn, _ = linked(session, household)
    failure = TokenInvalid("HTTP 401") if status == 401 else UpstreamError(429, retry_after=600)
    calls = []

    class Client:
        def accounts(self, token):
            calls.append("discovery")
            if phase == "discovery":
                raise failure
            return [discovery.DiscoveredAccount("1", "Account 1", category_uid="category-1")]

        def balance(self, *args):
            calls.append("balance")
            raise failure

        def feed_items(self, *args):
            calls.append("feed")
            return []

    before = datetime.now()
    if manual:
        monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")
        with pytest.raises(HTTPException) as exc:
            bankapi.sync_transactions(
                conn.id, household, session, "synthetic", None, Client(), request())
        assert exc.value.status_code == (422 if status == 401 else 429)
    else:
        assert scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic") == 0
    assert "feed" not in calls
    assert conn.status is (ConnStatus.revoked if status == 401 else ConnStatus.degraded)
    assert conn.last_error
    if status == 429:
        cursor = sync.cursor_for(session, account.id, TransactionSource.starling)
        assert cursor.status is SyncStatus.failed
        assert cursor.retry_at >= before + timedelta(seconds=590)
        assert cursor.refresh_requested
        calls.clear()
        assert scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic") == 0
        assert calls == []


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("status", [401, 429])
def test_feed_rejection_stops_sibling_requests(session, household, monkeypatch, manual, status):
    a1, conn, _ = linked(session, household)
    a2, _, m2 = linked(session, household, "2")
    m2.connection_id = conn.id
    session.commit()
    calls = []

    class Client:
        def accounts(self, *args):
            return [discovery.DiscoveredAccount(str(i), f"Account {i}",
                                                category_uid=f"category-{i}")
                    for i in (1, 2)]

        def balance(self, *args):
            return 0

        def feed_items(self, token, uid, *args):
            calls.append(uid)
            if uid == "1":
                if status == 401:
                    raise TokenInvalid("HTTP 401")
                raise UpstreamError(429, retry_after=600)
            return []

    before = datetime.now()
    if manual:
        monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")
        bankapi.sync_transactions(
            conn.id, household, session, "synthetic", None, Client(), request())
    else:
        scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic")
    assert calls == ["1"]
    if status == 429:
        for account in (a1, a2):
            cursor = sync.cursor_for(session, account.id, TransactionSource.starling)
            assert cursor.retry_at >= before + timedelta(seconds=590)
            assert cursor.status is SyncStatus.failed
            assert cursor.refresh_requested


@pytest.mark.parametrize("manual", [False, True])
def test_denied_spaces_retry_at_normal_cadence_without_fake_success(
        session, household, monkeypatch, manual):
    account, conn, mapping = linked(session, household)
    mapping.external_kind = "space"
    session.commit()
    calls = []

    class Client:
        unavailable_space_accounts = {"1"}

        def accounts(self, token):
            calls.append("discovery")
            listing = discovery.DiscoveryListing()
            listing.unavailable_space_accounts.add("1")
            return listing

        def balance(self, *args):
            pytest.fail("denied Spaces balance was retried")

        def feed_items(self, *args):
            pytest.fail("denied Spaces feed was retried")

    before = datetime.now()
    if manual:
        monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")
        bankapi.sync_transactions(
            conn.id, household, session, "synthetic", None, Client(), request())
    else:
        scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic",
                            interval_seconds=1800)
    cursor = sync.cursor_for(session, account.id, TransactionSource.starling)
    assert cursor.status is SyncStatus.failed
    assert "Spaces" in cursor.error
    assert cursor.last_success_at is None
    assert cursor.retry_at >= before + timedelta(seconds=1790)
    scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic",
                        interval_seconds=1800)
    assert calls == ["discovery"]


@pytest.mark.parametrize("endpoint,payload", [
    ("accounts", {}), ("accounts", {"accounts": {}}),
    ("accounts", {"accounts": [None]}),
    ("spaces", {}), ("spaces", {"savingsGoals": {}}),
    ("spaces", {"savingsGoals": [None]}),
])
def test_malformed_provider_listing_never_closes_mappings(
        session, household, monkeypatch, endpoint, payload):
    _, conn, mapping = linked(session, household)
    mapping.external_kind = "space"
    session.commit()

    def get(token, path):
        if path.endswith(f"/{endpoint}"):
            return payload
        return {"accounts": [{"accountUid": "1", "defaultCategory": "main"}]}

    monkeypatch.setattr(starling, "_get", get)
    with pytest.raises(UpstreamError) as exc:
        discovery.discover(session, conn, starling.StarlingClient(), "synthetic")
    assert exc.value.status == 502
    assert mapping.status.value == "linked"


@pytest.mark.parametrize("category", [None, "", 123, "   "])
def test_incomplete_main_category_preserves_existing_mapping(
        session, household, monkeypatch, category):
    _, conn, mapping = linked(session, household)
    session.commit()

    def get(token, path):
        if path.endswith("/spaces"):
            return {"savingsGoals": [], "spendingSpaces": []}
        account = {"accountUid": "1"}
        if category is not None:
            account["defaultCategory"] = category
        return {"accounts": [account]}

    monkeypatch.setattr(starling, "_get", get)
    with pytest.raises(UpstreamError) as exc:
        discovery.discover(session, conn, starling.StarlingClient(), "synthetic")
    assert exc.value.status == 502
    assert mapping.status.value == "linked"
    assert mapping.external_category_uid == "category-1"


@pytest.mark.parametrize("endpoint", ["accounts", "spaces"])
@pytest.mark.parametrize("identity", [123, "   "])
def test_invalid_provider_identity_preserves_existing_mapping(
        session, household, monkeypatch, endpoint, identity):
    _, conn, mapping = linked(session, household)
    if endpoint == "spaces":
        mapping.external_kind = "space"
    session.commit()

    def get(token, path):
        if path.endswith("/accounts"):
            return {"accounts": [{"accountUid": identity if endpoint == "accounts" else "1",
                                   "defaultCategory": "category-1"}]}
        return {"savingsGoals": [{"savingsGoalUid": identity}]} if endpoint == "spaces" else {
            "savingsGoals": []}

    monkeypatch.setattr(starling, "_get", get)
    with pytest.raises(UpstreamError) as exc:
        discovery.discover(session, conn, starling.StarlingClient(), "synthetic")
    assert exc.value.status == 502
    assert mapping.status.value == "linked"
    assert mapping.external_uid == "1"
    assert mapping.external_category_uid == "category-1"


@pytest.mark.parametrize("manual", [False, True])
def test_delayed_spaces_permission_retry_keeps_main_feed_usable(
        session, household, monkeypatch, manual):
    _, conn, space = linked(session, household)
    space.external_kind = "space"
    account, _, main = linked(session, household, "2")
    main.connection_id = conn.id
    main.external_uid = "1"
    main.external_category_uid = "main-1"
    main.external_kind = "account"
    session.commit()
    calls = []

    class Client:
        unavailable_space_accounts = {"1"}

        def accounts(self, *args):
            calls.append("discovery")
            listing = discovery.DiscoveryListing([
                discovery.DiscoveredAccount("1", "Main", category_uid="main-1")])
            listing.unavailable_space_accounts.add("1")
            return listing

        def balance(self, *args):
            return 0

        def feed_items(self, token, uid, category, since):
            assert category == "main-1"
            calls.append("main feed")
            return []

    monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")

    def run():
        if manual:
            bankapi.sync_transactions(
                conn.id, household, session, "synthetic", None, Client(), request())
        else:
            scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic",
                                interval_seconds=1800)

    run()
    main_cursor = sync.cursor_for(session, account.id, TransactionSource.starling)
    main_cursor.last_success_at = datetime.now() - timedelta(hours=1)
    session.commit()
    run()
    assert calls == ["discovery", "main feed", "main feed"]


@pytest.mark.parametrize("manual", [False, True])
def test_provider_rate_limit_also_blocks_newly_linked_siblings(
        session, household, monkeypatch, manual):
    _, conn, _ = linked(session, household)
    banks.record_api_error(session, conn, 429, "HTTP 429", retry_after=600)
    _, _, sibling = linked(session, household, "2")
    sibling.connection_id = conn.id
    session.commit()

    class Client:
        def accounts(self, *args):
            pytest.fail("connection rate limit allowed discovery")

        def feed_items(self, *args):
            pytest.fail("connection rate limit allowed a new sibling feed")

    if manual:
        monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")
        bankapi.sync_transactions(
            conn.id, household, session, "synthetic", None, Client(), request())
    else:
        scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic")


def test_permission_delay_does_not_extend_provider_retry_after(session, household):
    _, conn, space = linked(session, household)
    space.external_kind = "space"
    conn.last_error = "Spaces unavailable — check Spaces read permission"
    discovery.defer_unavailable_spaces(session, conn, {"1"}, 1800)
    before = datetime.now()
    banks.record_api_error(session, conn, 429, "HTTP 429", retry_after=600)
    retry_at = banks.connection_retry_at(session, conn)
    assert before + timedelta(seconds=590) <= retry_at <= before + timedelta(seconds=610)


@pytest.mark.parametrize("endpoint", ["discover", "sync_balances"])
def test_direct_provider_routes_honor_connection_cooldown(
        session, household, monkeypatch, endpoint):
    _, conn, _ = linked(session, household)
    banks.record_api_error(session, conn, 429, "HTTP 429", retry_after=600)
    session.commit()
    monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")

    class Client:
        def accounts(self, *args):
            pytest.fail("Discover bypassed provider cooldown")

        def balance(self, *args):
            pytest.fail("Balances bypassed provider cooldown")

    with pytest.raises(HTTPException) as exc:
        getattr(bankapi, endpoint)(conn.id, household, session, "synthetic", None, Client())
    assert exc.value.status_code == 429


@pytest.mark.parametrize("endpoint", ["discover", "sync_balances"])
@pytest.mark.parametrize("status", [401, 429])
def test_direct_provider_routes_persist_failures(session, household, monkeypatch, endpoint, status):
    account, conn, _ = linked(session, household)
    monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")
    failure = TokenInvalid("HTTP 401") if status == 401 else UpstreamError(429, retry_after=600)

    class Client:
        def accounts(self, *args):
            raise failure

        def balance(self, *args):
            raise failure

    before = datetime.now()
    with pytest.raises(HTTPException) as exc:
        getattr(bankapi, endpoint)(conn.id, household, session, "synthetic", None, Client())
    assert exc.value.status_code == (422 if status == 401 else 429)
    assert conn.status is (ConnStatus.revoked if status == 401 else ConnStatus.degraded)
    if status == 429:
        cursor = sync.cursor_for(session, account.id, TransactionSource.starling)
        assert cursor.retry_at >= before + timedelta(seconds=590)


def test_historical_sync_waits_for_provider_retry_without_calls(session, household, monkeypatch):
    _, conn, _ = linked(session, household)
    banks.record_api_error(session, conn, 429, "HTTP 429", retry_after=600)
    monkeypatch.setattr(bankapi, "require_admin", lambda *args: None)

    class Client:
        def accounts(self, *args):
            pytest.fail("historical sync bypassed provider cooldown")

    with pytest.raises(HTTPException) as exc:
        bankapi.sync_transactions(conn.id, household, session, "synthetic", None,
                                  Client(), request(),
                                  historical_since=date(2020, 1, 1))
    assert exc.value.status_code == 409


def test_provider_cooldown_persists_before_any_account_is_linked(session, household):
    _, conn, mapping = linked(session, household)
    mapping.status = bankapi.MappingStatus.proposed
    session.commit()
    before = datetime.now()
    banks.record_api_error(session, conn, 429, "HTTP 429", retry_after=600)
    session.commit()
    assert banks.connection_retry_at(session, conn) >= before + timedelta(seconds=590)
    row = _status(request(), session, household.id).connections[0]
    assert row.retry_at >= before + timedelta(seconds=590)
    assert row.next_due_at == row.retry_at


@pytest.mark.parametrize("mode", ["rotate", "revive"])
def test_successful_reauthentication_clears_connection_deadline(session, household, mode):
    _, conn, _ = linked(session, household)
    banks.record_api_error(session, conn, 429, "HTTP 429", retry_after=600)
    session.commit()
    assert banks.connection_retry_at(session, conn) is not None
    ring = vault.Keyring({1: b"0" * 32})
    calls = []

    def validate(token):
        calls.append(token)
        return banks.TokenInfo(scopes=banks.REQUIRED_SCOPES)

    if mode == "rotate":
        banks.rotate(session, conn, "synthetic-new-token", validate, ring)
    else:
        banks.connect(session, household.id, conn.provider, "synthetic-new-token",
                       validate, ring, existing=conn)
    assert calls == ["synthetic-new-token"]
    assert banks.connection_retry_at(session, conn) is None
    assert appconfig.get_value(session, f"{banks.RETRY_KEY_PREFIX}{conn.id}") is None


@pytest.mark.parametrize("raw", ["invalid timestamp", "2000-01-01T00:00:00"])
def test_connection_deadline_reads_do_not_mutate_metadata(session, household, raw):
    _, conn, _ = linked(session, household)
    key = f"{banks.RETRY_KEY_PREFIX}{conn.id}"
    appconfig.set_value(session, key, raw)
    session.commit()
    assert banks.connection_retry_at(session, conn) is None
    assert appconfig.get_value(session, key) == raw
    assert not session.new and not session.dirty and not session.deleted


@pytest.mark.parametrize("historical", [False, True])
def test_sync_before_linking_honors_stored_provider_deadline(
        session, household, monkeypatch, historical):
    _, conn, mapping = linked(session, household)
    mapping.status = bankapi.MappingStatus.proposed
    banks.record_api_error(session, conn, 429, "HTTP 429", retry_after=600)
    session.commit()
    monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")
    monkeypatch.setattr(bankapi, "require_admin", lambda *args: None)

    class Client:
        def accounts(self, *args):
            pytest.fail("Sync before linking bypassed provider cooldown")

    if historical:
        with pytest.raises(HTTPException) as exc:
            bankapi.sync_transactions(
                conn.id, household, session, "synthetic", None, Client(), request(),
                historical_since=date(2020, 1, 1))
        assert exc.value.status_code == 409
    else:
        result = bankapi.sync_transactions(
            conn.id, household, session, "synthetic", None, Client(), request())
        assert result["runs"] == []
        assert conn.status is ConnStatus.degraded


def test_direct_balances_optional_spaces_403_keeps_main_connection_usable(
        session, household, monkeypatch):
    _, conn, space = linked(session, household)
    space.external_kind = "space"
    space.balance_pence = 1234
    _, _, main = linked(session, household, "2")
    main.connection_id = conn.id
    main.external_kind = "account"
    main.external_uid = "1"
    main.external_category_uid = "main-1"
    session.commit()
    monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")

    def get(token, path):
        if path.endswith("/spaces"):
            raise starling.TokenHttpError(403)
        assert path.endswith("/balance")
        return {"effectiveBalance": {"minorUnits": 5000}}

    monkeypatch.setattr(starling, "_get", get)
    result = bankapi.sync_balances(
        conn.id, household, session, "synthetic", None, starling.StarlingClient())
    assert result == {"synced": 1}
    assert conn.status is ConnStatus.degraded
    assert "Spaces" in conn.last_error
    assert space.balance_pence == 1234
    assert main.balance_pence == 5000


def test_missing_space_never_receives_parent_account_balance(session, household, monkeypatch):
    _, conn, space = linked(session, household)
    space.external_kind = "space"
    space.balance_pence = 1234
    session.commit()
    monkeypatch.setattr(bankapi.banks, "token", lambda *args: "synthetic")
    calls = []

    def get(token, path):
        calls.append(path)
        if path.endswith("/spaces"):
            return {"savingsGoals": [], "spendingSpaces": []}
        return {"effectiveBalance": {"minorUnits": 99999}}

    monkeypatch.setattr(starling, "_get", get)
    with pytest.raises(HTTPException) as exc:
        bankapi.sync_balances(
            conn.id, household, session, "synthetic", None, starling.StarlingClient())
    assert exc.value.status_code == 502
    assert calls == ["/api/v2/account/1/spaces"]
    assert space.balance_pence == 1234
    assert conn.status is ConnStatus.degraded
