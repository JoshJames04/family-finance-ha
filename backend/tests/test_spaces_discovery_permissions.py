"""Optional Spaces permissions never imply provider-side account closure."""

import io
import json
import urllib.error
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.v1 import banks as bankapi
from app.core import scheduler
from app.core.settings import Settings
from app.domain import discovery, sync
from app.domain.banks import TokenInvalid
from app.integrations import starling
from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    BankProvider,
    ConnStatus,
    MappingStatus,
    TransactionSource,
)


def linked_spaces(session, household):
    conn = BankConnection(
        household_id=household.id,
        provider=BankProvider.starling,
        vault_ref="synthetic",
        status=ConnStatus.connected,
    )
    session.add(conn)
    session.flush()
    mappings = []
    for uid in ("a", "b"):
        account = Account(
            household_id=household.id,
            name=f"Space {uid}",
            type="personal",
            opening_date=date(2026, 1, 1),
            opening_balance_pence=0,
        )
        session.add(account)
        session.flush()
        mapping = BankAccountMapping(
            household_id=household.id,
            connection_id=conn.id,
            external_uid=uid,
            external_category_uid=f"space-{uid}",
            external_kind="space",
            external_label=account.name,
            account_id=account.id,
            status=MappingStatus.linked,
        )
        session.add(mapping)
        mappings.append(mapping)
    session.commit()
    return conn, mappings


def urllib_provider(monkeypatch, *, denied_path, status):
    def urlopen(request, **kwargs):
        path = request.full_url.removeprefix(starling.API_BASE)
        if path == denied_path:
            raise urllib.error.HTTPError(request.full_url, status, "Synthetic denial", {}, None)
        payload = (
            {
                "accounts": [
                    {"accountUid": "a", "defaultCategory": "main-a"},
                    {"accountUid": "b", "defaultCategory": "main-b"},
                ]
            }
            if path.endswith("/accounts")
            else {"savingsGoals": [], "spendingSpaces": []}
        )
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(starling.urllib.request, "urlopen", urlopen)


def test_real_optional_spaces_403_preserves_denied_parent_spaces_only(
    session, household, monkeypatch
):
    conn, (denied, complete) = linked_spaces(session, household)
    original_account_id = denied.account_id
    urllib_provider(monkeypatch, denied_path="/api/v2/account/a/spaces", status=403)
    rows = discovery.discover(session, conn, starling.StarlingClient(), "synthetic")
    assert denied.status is MappingStatus.linked
    assert denied.account_id == original_account_id
    assert complete.status is MappingStatus.closed
    assert {
        (m.external_uid, m.external_category_uid) for m in rows if m.external_kind == "account"
    } == {("a", "main-a"), ("b", "main-b")}
    assert conn.status is ConnStatus.degraded


@pytest.mark.parametrize(
    "path,status", [("/api/v2/account/a/spaces", 401), ("/api/v2/accounts", 403)]
)
def test_required_permission_failures_remain_token_invalid(
    session, household, monkeypatch, path, status
):
    conn, mappings = linked_spaces(session, household)
    urllib_provider(monkeypatch, denied_path=path, status=status)
    with pytest.raises(TokenInvalid):
        discovery.discover(session, conn, starling.StarlingClient(), "synthetic")
    assert all(m.status is MappingStatus.linked for m in mappings)


def test_adapter_optional_403_marks_incomplete_listing(session, household, monkeypatch):
    conn, (denied, complete) = linked_spaces(session, household)

    def get(token, path):
        if path == "/api/v2/account/a/spaces":
            raise starling.UpstreamError(403)
        return (
            {
                "accounts": [
                    {"accountUid": "a", "defaultCategory": "main-a"},
                    {"accountUid": "b", "defaultCategory": "main-b"},
                ]
            }
            if path.endswith("/accounts")
            else {"savingsGoals": [], "spendingSpaces": []}
        )

    monkeypatch.setattr(starling, "_get", get)
    discovery.discover(session, conn, starling.StarlingClient(), "synthetic")
    assert denied.status is MappingStatus.linked
    assert complete.status is MappingStatus.closed


def test_optional_denial_keeps_space_balance_and_refreshes_main_balance(
    session, household, monkeypatch
):
    conn, (denied, _) = linked_spaces(session, household)
    denied.balance_pence = 1234
    main = BankAccountMapping(
        household_id=household.id,
        connection_id=conn.id,
        external_uid="a",
        external_category_uid="main-a",
        external_kind="account",
        external_label="Main",
        status=MappingStatus.linked,
    )
    session.add(main)
    session.commit()

    def get(token, path):
        if path == "/api/v2/account/a/spaces":
            raise starling.TokenHttpError(403)
        if path.endswith("/balance"):
            return {"effectiveBalance": {"minorUnits": 5000}}
        return {"accounts": [{"accountUid": "a", "defaultCategory": "main-a"}]}

    monkeypatch.setattr(starling, "_get", get)
    client = starling.StarlingClient()
    discovery.discover(session, conn, client, "synthetic")
    assert discovery.sync_balances(session, conn, client, "synthetic") == 1
    assert denied.balance_pence == 1234
    assert main.balance_pence == 5000
    assert conn.status is ConnStatus.degraded
    assert "Spaces" in conn.last_error


@pytest.mark.parametrize("manual", [False, True])
def test_partial_permission_sync_recovers_without_leaking_between_connections(
    session, household, monkeypatch, manual
):
    first, (denied, _) = linked_spaces(session, household)
    denied.balance_pence = 1234
    second = BankConnection(
        household_id=household.id,
        provider=BankProvider.starling,
        vault_ref="synthetic-second",
        status=ConnStatus.degraded,
        last_error="old error",
    )
    session.add(second)
    session.flush()
    for conn, category, kind in [
        (first, "main-a", "account"),
        (second, "main-a", "account"),
        (second, "space-a", "space"),
    ]:
        account = Account(
            household_id=household.id,
            name=f"{conn.id}-{category}",
            type="personal",
            opening_date=date(2026, 1, 1),
            opening_balance_pence=0,
        )
        session.add(account)
        session.flush()
        session.add(
            BankAccountMapping(
                household_id=household.id,
                connection_id=conn.id,
                external_uid="a",
                external_category_uid=category,
                external_kind=kind,
                external_label=account.name,
                account_id=account.id,
                status=MappingStatus.linked,
            )
        )
    session.commit()
    recovered = False
    feeds = []

    def get(token, path):
        if path.endswith("/spaces"):
            if token == "denied" and not recovered:
                raise starling.TokenHttpError(403)
            return {
                "savingsGoals": [
                    {
                        "savingsGoalUid": "space-a",
                        "name": f"Space {token}",
                        "totalSaved": {"minorUnits": 1234},
                    }
                ]
            }
        if path.endswith("/balance"):
            return {"effectiveBalance": {"minorUnits": 5000}}
        if "/feed/" in path:
            if token == "denied" and not recovered and "/space-a?" in path:
                pytest.fail("denied optional space feed was requested")
            feeds.append((token, "space" if "/space-a?" in path else "main"))
            return {"feedItems": []}
        return {
            "accounts": [{"accountUid": "a", "defaultCategory": "main-a", "name": f"Main {token}"}]
        }

    monkeypatch.setattr(starling, "_get", get)
    client = starling.StarlingClient()

    def token_for(_, conn):
        return "denied" if conn.id == first.id else "allowed"

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=Settings())))
    if manual:
        monkeypatch.setattr(
            bankapi.banks, "token", lambda session, conn, ring: token_for(session, conn)
        )
        for conn in (first, second):
            bankapi.sync_transactions(
                conn.id, household, session, "synthetic", None, client, request
            )
    else:
        scheduler.sync_tick(session, client=client, token_for=token_for, interval_seconds=0)
    assert first.status is ConnStatus.degraded
    assert "Spaces" in first.last_error
    assert second.status is ConnStatus.connected
    assert second.last_error is None
    assert denied.balance_pence == 1234
    assert ("denied", "main") in feeds
    assert ("allowed", "space") in feeds
    recovered = True
    denied_cursor = sync.cursor_for(session, denied.account_id, TransactionSource.starling)
    denied_cursor.retry_at = datetime.now() - timedelta(seconds=1)
    session.commit()
    if manual:
        bankapi.sync_transactions(
            first.id, household, session, "synthetic", None, client, request
        )
    else:
        scheduler.sync_tick(session, client=client, token_for=token_for, interval_seconds=0)
    assert first.status is ConnStatus.connected
    assert first.last_error is None
    assert ("denied", "space") in feeds
