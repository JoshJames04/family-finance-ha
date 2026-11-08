"""StarlingClient: account + space discovery shapes.

The spaces endpoint is the only place joint/shared pots surface —
they are categories on the parent account, never rows in /accounts.
"""
from __future__ import annotations

import pytest

from app.integrations import starling
from app.integrations.starling import StarlingClient, UpstreamError

ACCOUNTS = {"accounts": [{
    "accountUid": "uid-1", "name": "Personal",
    "defaultCategory": "cat-main"}]}
SPACES = {"savingsGoals": [
    {"savingsGoalUid": "cat-space", "name": "Joint Bills",
     "state": "ACTIVE",
     "totalSaved": {"currency": "GBP", "minorUnits": 40000}},
    {"savingsGoalUid": "cat-archived", "name": "Old",
     "state": "ARCHIVED"}],
    "spendingSpaces": [
    {"spaceUid": "cat-spend", "name": "Holiday",
     "state": "ACTIVE",
     "balance": {"currency": "GBP", "minorUnits": 5000}}]}


def _fake_get(payloads: dict[str, object]):
    def get(token: str, path: str) -> dict[str, object]:
        for k, v in payloads.items():
            if k in path:
                return v  # type: ignore[return-value]
        if isinstance(next(iter(payloads.values())), Exception):
            raise next(iter(payloads.values()))  # type: ignore[misc]
        raise UpstreamError(404, f"no stub for {path}")
    return get


def test_accounts_includes_spaces(monkeypatch: pytest.MonkeyPatch
                                  ) -> None:
    monkeypatch.setattr(starling, "_get", _fake_get({
        "/api/v2/accounts": ACCOUNTS, "spaces": SPACES}))
    rows = StarlingClient().accounts("pat")
    assert len(rows) == 3  # account + ACTIVE savings goal + spending space
    space = next(r for r in rows
                 if r.kind == "space" and r.category_uid == "cat-space")
    assert space.external_uid == "uid-1"       # parent account uid
    assert space.label == "Joint Bills"
    assert space.balance_pence == 40000
    spend = next(r for r in rows if r.category_uid == "cat-spend")
    assert spend.label == "Holiday" and spend.balance_pence == 5000
    assert all(r.label != "Old" for r in rows)


def test_feed_items_urlencodes_changes_since(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The sync watermark ends ``+00:00``/``Z`` — unencoded ``+``
    decodes to a space on the wire and Starling 404s the request."""
    seen: list[str] = []
    def get(token: str, path: str) -> dict[str, object]:
        seen.append(path)
        return {"feedItems": []}
    monkeypatch.setattr(starling, "_get", get)
    StarlingClient().feed_items(
        "pat", "uid-1", "cat-1", "2026-11-08T11:53:36.78+00:00")
    assert "changesSince=" in seen[0]
    assert "+" not in seen[0].split("changesSince=", 1)[1]
    assert "%2B00%3A00" in seen[0]


def test_spaces_failure_degrades_not_fails(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A token without space:read still discovers the accounts."""
    def get(token: str, path: str) -> dict[str, object]:
        if "spaces" in path:
            raise UpstreamError(403, "insufficient_scope")
        return ACCOUNTS
    monkeypatch.setattr(starling, "_get", get)
    rows = StarlingClient().accounts("pat")
    assert len(rows) == 1 and rows[0].kind == "account"


def test_balance_resolves_space_by_category(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(starling, "_get", _fake_get({
        "spaces": SPACES}))
    c = StarlingClient()
    assert c.balance("pat", "uid-1", "cat-space") == 40000
    assert c.balance("pat", "uid-1", "cat-spend") == 5000
