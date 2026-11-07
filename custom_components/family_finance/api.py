"""Async client for the Family Finance REST API (issue #100).

Thin wrapper around the app's ``/api/v1`` surface. Read-only — the
integration never mutates financial records.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import aiohttp

from .const import API_TIMEOUT, CONF_HOUSEHOLD_ID, CONF_TOKEN, CONF_URL


class FamilyFinanceError(Exception):
    """Base error — connection/transport/HTTP failure."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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
                        f"auth rejected ({resp.status})",
                        status=resp.status)
                if resp.status >= 400:
                    raise FamilyFinanceError(
                        f"HTTP {resp.status}", status=resp.status)
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise FamilyFinanceError(str(exc)) from exc

    async def _get_optional(self, path: str) -> Any | None:
        """GET that returns None on 404 — a household may simply not
        have the resource yet (e.g. no approved budget)."""
        try:
            return await self._get(path)
        except FamilyFinanceAuthError:
            raise
        except FamilyFinanceError as exc:
            if exc.status == 404:
                return None
            raise

    async def async_validate(self) -> dict[str, Any]:
        """Config-flow check: app reachable + credentials accepted."""
        return await self._get("/api/v1/version")

    async def async_fetch_summary(self) -> dict[str, Any]:
        """Poll payload for coordinator consumers (issue #101).

        Every field maps to a documented API response field; anything
        monetary stays in pence so sensors decide their own units.
        """
        today = date.today()
        version = await self._get("/api/v1/version")
        accounts = await self._get("/api/v1/accounts")
        acct_rows = accounts.get("items", [])
        total_balance = 0
        for a in acct_rows:
            bal = await self._get(
                f"/api/v1/accounts/{a['id']}/balance")
            total_balance += int(bal.get("balance_pence", 0))

        window_end = today + timedelta(days=60)
        movements = await self._get_optional(
            f"/api/v1/expected?start={today}&end={window_end}") or []
        bills = [m for m in movements if m.get("amount_pence", 0) < 0]
        paydays = sorted(
            m["expected_date"] for m in movements
            if m.get("source_type") == "payroll_run")

        spend = remaining = variance = None
        budgets = await self._get_optional("/api/v1/budgets")
        current = next(
            (b for b in (budgets or {}).get("items", [])
             if b["status"] == "approved"
             and b["start_date"] <= str(today) <= b["end_date"]),
            None)
        if current is not None:
            report = await self._get_optional(
                f"/api/v1/budgets/{current['id']}/report")
            if report is not None:
                lines = [l for l in report.get("lines", [])
                         if l.get("effective_pence") is not None]
                spend = sum(int(l["actual_pence"]) for l in lines)
                remaining = sum(
                    int(l["effective_pence"]) - int(l["actual_pence"])
                    for l in lines)
                variance = sum(int(l["variance_pence"]) for l in lines)

        goals = await self._get_optional("/api/v1/goals")
        goal_rows = (goals or {}).get("items", [])
        on_track = 0
        for g in goal_rows:
            prog = await self._get_optional(
                f"/api/v1/goals/{g['id']}/progress")
            if prog and prog.get("on_track"):
                on_track += 1

        outstanding = await self._get_optional(
            "/api/v1/expected/outstanding") or []

        return {
            "app_version": version.get("version", "unknown"),
            "account_count": len(acct_rows),
            "total_balance_pence": total_balance,
            "connected_accounts": sum(
                1 for a in acct_rows if a.get("institution_ref")),
            "sync_status": "connected" if any(
                a.get("institution_ref") for a in acct_rows
            ) else "manual",
            "upcoming_bills_count": len(bills),
            "next_payday": paydays[0] if paydays else None,
            "month_spend_pence": spend,
            "month_remaining_pence": remaining,
            "budget_variance_pence": variance,
            "goals_on_track": on_track,
            "goals_total": len(goal_rows),
            "outstanding_commitments": len(outstanding),
        }
