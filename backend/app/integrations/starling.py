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
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta

from app.domain.banks import (
    ConnError,
    TokenInfo,
    TokenInvalid,
    ValidatorUnavailable,
)
from app.domain.discovery import (
    BankClient,
    DiscoveredAccount,
    DiscoveryListing,
    SpacesUnavailable,
)
from app.domain.sync import (
    FeedItem,
    FeedPage,
    RateLimited,
    SyncError,
)
from app.persistence.models import SyncCursor, TransactionStatus

API_BASE = "https://api.starlingbank.com"


class UpstreamError(ConnError):
    """HTTP error carrying the status code for state mapping."""

    def __init__(self, status: int, message: str = "",
                 retry_after: float | None = None) -> None:
        super().__init__(message or f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


class TokenHttpError(TokenInvalid):
    """Authentication failure retaining its provider HTTP status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
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
            raise TokenHttpError(e.code) from e
        retry = None
        try:
            retry = float(e.headers.get("Retry-After", "") or "nan")
        except (TypeError, ValueError):
            retry = None
        raise UpstreamError(
            e.code, retry_after=retry if retry == retry else None
            ) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ValidatorUnavailable(str(e)) from e


class StarlingClient:
    """Production BankClient — resolves the token per call."""

    def __init__(self) -> None:
        # One client instance serves a whole request; caching the
        # spaces listing avoids re-fetching it once per space balance
        # inside a single /sync (issue #316 — rate-limit pressure).
        self._spaces_cache: dict[str, list[DiscoveredAccount]] = {}
        self.unavailable_space_accounts: set[str] = set()

    def validate_token(self, token: str) -> TokenInfo:
        data = _get(token, "/api/v2/identity/token")
        scopes = data.get("scopes", [])
        return TokenInfo(
            scopes=frozenset(scopes) if isinstance(
                scopes, list) else frozenset())

    def accounts(self, token: str) -> list[DiscoveredAccount]:
        self.unavailable_space_accounts.clear()
        self._spaces_cache.clear()
        data = _get(token, "/api/v2/accounts")
        rows = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(rows, list) or any(
                not isinstance(a, dict) or not isinstance(a.get("accountUid"), str)
                or not a["accountUid"].strip()
                or not isinstance(a.get("defaultCategory"), str)
                or not a["defaultCategory"].strip() for a in rows):
            raise UpstreamError(502, "malformed accounts listing")
        out = DiscoveryListing(
            DiscoveredAccount(
                external_uid=str(a["accountUid"]),
                label=str(a.get("name") or "Starling account"),
                kind="account",
                category_uid=str(a["defaultCategory"])
                if a.get("defaultCategory") else None)
            for a in rows if isinstance(a, dict))
        # Spaces are categories, not accounts — /accounts never lists
        # them. Their spaceUid doubles as the feed categoryUid, so a
        # space maps and syncs exactly like an account row.
        for a in rows:
            if not isinstance(a, dict) or not a.get("accountUid"):
                continue
            uid = str(a["accountUid"])
            try:
                spaces = self.spaces(token, uid)
            except UpstreamError as e:
                if e.status != 403:
                    raise  # incomplete discovery must not close existing spaces
                out.unavailable_space_accounts.add(uid)
                self.unavailable_space_accounts.add(uid)
                continue  # token lacks space:read — preserve account discovery
            out.extend(spaces)
        return out

    def spaces(self, token: str, account_uid: str
               ) -> list[DiscoveredAccount]:
        """GET /account/{uid}/spaces — savings + spending spaces with
        per-space balance. The payload splits them: ``savingsGoals``
        use ``savingsGoalUid``, ``spendingSpaces`` use ``spaceUid`` —
        both are the feed ``categoryUid``."""
        cached = self._spaces_cache.get(account_uid)
        if cached is not None:
            return cached
        try:
            data = _get(token, f"/api/v2/account/{account_uid}/spaces")
        except TokenHttpError as e:
            if e.status != 403:
                raise
            raise UpstreamError(403, "token lacks Spaces read permission") from e
        if not isinstance(data, dict) or not any(
                key in data for key in ("savingsGoals", "spendingSpaces")):
            raise UpstreamError(502, "malformed Spaces listing")
        out = []
        for key in ("savingsGoals", "spendingSpaces"):
            rows = data.get(key, [])
            if not isinstance(rows, list):
                raise UpstreamError(502, "malformed Spaces listing")
            for s in rows:
                if not isinstance(s, dict):
                    raise UpstreamError(502, "malformed Spaces listing")
                uid = s.get("savingsGoalUid") or s.get("spaceUid")
                if not isinstance(uid, str) or not uid.strip():
                    raise UpstreamError(502, "malformed Spaces listing")
                if s.get("state") not in (None, "ACTIVE"):
                    continue
                bal = s.get("balance") or s.get("totalSaved") or {}
                pence = (int(str(bal["minorUnits"]))
                         if isinstance(bal, dict) and "minorUnits" in bal
                         else None)
                out.append(DiscoveredAccount(
                    external_uid=str(account_uid),
                    label=str(s.get("name") or "Space"),
                    kind="space",
                    balance_pence=pence,
                    category_uid=str(uid)))
        self._spaces_cache[account_uid] = out
        return out

    def balance(self, token: str, external_uid: str,
                category_uid: str | None = None) -> int:
        """Whole-account balance via /accounts/{uid}/balance, or a
        space's balance when ``category_uid`` names one."""
        if category_uid is not None:
            try:
                spaces = self.spaces(token, external_uid)
            except UpstreamError as e:
                if e.status != 403:
                    raise
                self.unavailable_space_accounts.add(external_uid)
                raise SpacesUnavailable("token lacks Spaces read permission") from e
            for s in spaces:
                if s.category_uid == category_uid:
                    if s.balance_pence is not None:
                        return s.balance_pence
                    raise UpstreamError(502, "space carries no balance")
            raise UpstreamError(502, "space absent from provider listing — rediscover accounts")
        data = _get(
            token, f"/api/v2/accounts/{external_uid}/balance")
        # effectiveBalance (cleared + pending unsettled) is what the
        # Starling app displays — always sync that figure.
        effective = data.get("effectiveBalance", {})
        if not isinstance(effective, dict) \
                or "minorUnits" not in effective:
            raise UpstreamError(502, "malformed balance payload")
        return int(str(effective["minorUnits"]))

    def feed_items(self, token: str, external_uid: str,
                   category_uid: str, since: str
                   ) -> list[FeedItem]:
        """GET /feed/account/{a}/category/{c}?changesSince= — the
        incremental sync primitive from spike #64: items created or
        updated since the cursor. Maps direction/status onto our
        signed-amount + status conventions."""
        data = _get(
            token,
            f"/api/v2/feed/account/{external_uid}"
            f"/category/{category_uid}?changesSince="
            f"{urllib.parse.quote(since, safe='')}")
        items = data.get("feedItems", [])
        if not isinstance(items, list):
            return []
        return [i for i in (self._map(i2) for i2 in items
                          if isinstance(i2, dict)) if i is not None]

    @staticmethod
    def _map(i: dict[str, object]) -> FeedItem | None:
        uid = i.get("feedItemUid")
        if not isinstance(uid, str) or not uid:
            return None
        amount = i.get("amount", {})
        minor = int(str(amount.get("minorUnits", 0)
                        )) if isinstance(amount, dict) else 0
        direction = str(i.get("direction", "OUT"))
        signed = minor if direction == "IN" else -minor
        ts = str(i.get("transactionTime", ""))[:10]
        provider_cat = i.get("spendingCategory")
        return FeedItem(
            external_ref=uid,
            date=date.fromisoformat(ts) if ts else date.today(),
            amount_pence=signed,
            payee=str(i.get("counterPartyName", "")),
            status=_STATUS_MAP.get(str(i.get("status", ""))),
            provider_category=str(provider_cat)
            if isinstance(provider_cat, str) and provider_cat else None)


_STATUS_MAP = {
    "SETTLED": TransactionStatus.cleared,
    "REFUNDED": TransactionStatus.cleared,
    "PENDING": TransactionStatus.pending,
    "UPCOMING": TransactionStatus.pending,
    "RETRYING": TransactionStatus.pending,
    "REVERSED": TransactionStatus.void,
    "DECLINED": TransactionStatus.void,
}

BACKFILL_DAYS = 90  # initial changesSince window when no cursor


def starling_fetch(client: BankClient, token: str,
                   external_uid: str, category_uid: str, *,
                   historical_since: date | None = None,
                   on_error: Callable[[int | None, str, float | None], object] | None = None,
                   ) -> Callable[[SyncCursor], Iterator[FeedPage]]:
    """Adapt the client to the sync engine's ``fetch`` protocol:
    cursor ``last_ref`` is the changesSince watermark — absent → a
    bounded backfill start. The watermark is stamped pre-request so
    items updated during the fetch arrive next run."""
    def fetch(cursor: SyncCursor) -> Iterator[FeedPage]:
        since = (historical_since.isoformat() + "T00:00:00.000Z"
                 if historical_since is not None else cursor.last_ref) or (
            (date.today() - timedelta(days=BACKFILL_DAYS))
            .isoformat() + "T00:00:00.000Z")
        watermark = (datetime.now(UTC)
                     .strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        try:
            items = client.feed_items(token, external_uid,
                                      category_uid, since)
        except ConnError as e:
            status = getattr(e, "status", 401 if isinstance(e, TokenInvalid) else None)
            message = ("provider unavailable — check network/DNS"
                       if isinstance(e, ValidatorUnavailable) else str(e))
            if on_error is not None:
                on_error(status, message, e.retry_after if isinstance(e, UpstreamError) else None)
            if isinstance(e, UpstreamError) and e.status == 429:
                raise RateLimited(
                    str(e), retry_after=e.retry_after) from e
            raise SyncError(message) from e
        yield FeedPage(items, cursor_ref=watermark)
    return fetch
