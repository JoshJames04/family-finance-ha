"""Async client for the Family Finance REST API (issue #100).

Thin wrapper around the app's ``/api/v1`` surface. Read-only — the
integration never mutates financial records.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import API_TIMEOUT, CONF_HOUSEHOLD_ID, CONF_TOKEN, CONF_URL


class FamilyFinanceError(Exception):
    """Base error — connection/transport/HTTP failure."""


class FamilyFinanceAuthError(FamilyFinanceError):
    """Credentials rejected (401/403)."""


class FamilyFinanceClient:
    """Read-only API client scoped to one household."""

    def __init__(
        self, session: aiohttp.ClientSession, config: dict[str, Any]
    ) -> None:
        self._session = session
        self._base = str(config[CONF_URL]).rstrip("/")
        self._headers = {
            "X-Household-Id": str(config[CONF_HOUSEHOLD_ID]),
        }
        if token := config.get(CONF_TOKEN):
            self._headers["Authorization"] = f"Bearer {token}"

    async def _get(self, path: str) -> Any:
        try:
            async with self._session.get(
                f"{self._base}{path}",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                if resp.status in (401, 403):
                    raise FamilyFinanceAuthError(
                        f"auth rejected ({resp.status})")
                if resp.status >= 400:
                    raise FamilyFinanceError(f"HTTP {resp.status}")
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise FamilyFinanceError(str(exc)) from exc

    async def async_validate(self) -> dict[str, Any]:
        """Config-flow check: app reachable + credentials accepted."""
        return await self._get("/api/v1/version")

    async def async_fetch_summary(self) -> dict[str, Any]:
        """Poll payload for coordinator consumers."""
        version = await self._get("/api/v1/version")
        accounts = await self._get("/api/v1/accounts")
        return {
            "app_version": version.get("version", "unknown"),
            "account_count": len(accounts.get("items", [])),
        }
