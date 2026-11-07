"""Starling API client (stdlib urllib — no new deps).

Implements the surfaces the connection/discovery domains need per
spike #64: /accounts for discovery, /accounts/{uid}/balance for
balances, /identity/token for scope validation. Rate-limit and
error responses map onto the connection domain's failure taxonomy
so 401/403 revoke and 429/5xx degrade.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.domain.banks import (
    ConnError,
    TokenInfo,
    TokenInvalid,
    ValidatorUnavailable,
)
from app.domain.discovery import DiscoveredAccount

API_BASE = "https://api.starlingbank.com"


class UpstreamError(ConnError):
    """HTTP error carrying the status code for state mapping."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status}")
        self.status = status


def _get(token: str, path: str) -> dict[str, object]:
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data: dict[str, object] = json.loads(resp.read())
            return data
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TokenInvalid(f"HTTP {e.code}") from e
        raise UpstreamError(e.code) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ValidatorUnavailable(str(e)) from e


class StarlingClient:
    """Production BankClient — resolves the token per call."""

    def validate_token(self, token: str) -> TokenInfo:
        data = _get(token, "/api/v2/identity/token")
        scopes = data.get("scopes", [])
        return TokenInfo(
            scopes=frozenset(scopes) if isinstance(
                scopes, list) else frozenset())

    def accounts(self, token: str) -> list[DiscoveredAccount]:
        data = _get(token, "/api/v2/accounts")
        rows = data.get("accounts", [])
        if not isinstance(rows, list):
            return []
        return [
            DiscoveredAccount(
                external_uid=str(a["accountUid"]),
                label=str(a.get("name") or "Starling account"),
                kind="account")
            for a in rows if isinstance(a, dict)]

    def balance(self, token: str, external_uid: str) -> int:
        data = _get(
            token, f"/api/v2/accounts/{external_uid}/balance")
        cleared = data.get("clearedBalance", {})
        if not isinstance(cleared, dict):
            raise UpstreamError(502, "malformed balance payload")
        return int(str(cleared["minorUnits"]))
