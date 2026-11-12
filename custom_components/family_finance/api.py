"""Async client for the Family Finance REST API (issue #100).

Thin wrapper around the app API. Scheduled collection is read-only except
for idempotent, explicitly configured HA energy-statistics readings.
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
    """API client scoped to one household and explicit service permissions."""

    def __init__(self, session: aiohttp.ClientSession, config: dict[str, Any]) -> None:
        self._session = session
        self._base = str(config[CONF_URL]).rstrip("/")
        self._headers = {
            "X-Household-Id": str(config[CONF_HOUSEHOLD_ID]),
        }
        if token := config.get(CONF_TOKEN):
            self._headers["X-Admin-Token"] = str(token)

    async def _get(self, path: str) -> Any:
        try:
            async with self._session.get(
                f"{self._base}{path}",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                if resp.status in (401, 403):
                    raise FamilyFinanceAuthError(
                        f"auth rejected ({resp.status})", status=resp.status
                    )
                if resp.status >= 400:
                    raise FamilyFinanceError(f"HTTP {resp.status}", status=resp.status)
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise FamilyFinanceError("transport failure") from exc

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            async with self._session.post(
                f"{self._base}{path}",
                headers=self._headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                if resp.status in (401, 403):
                    raise FamilyFinanceAuthError(
                        f"auth rejected ({resp.status})", status=resp.status
                    )
                if resp.status >= 400:
                    raise FamilyFinanceError(f"HTTP {resp.status}", status=resp.status)
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise FamilyFinanceError("transport failure") from exc

    async def async_add_transaction(
        self,
        account_id: int,
        txn_date: date,
        amount_pence: int,
        payee: str,
        category_id: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """POST /transactions — audited app-side (issue #102)."""
        return await self._post(
            "/api/v1/transactions",
            {
                "account_id": account_id,
                "date": txn_date.isoformat(),
                "amount": amount_pence,
                "payee": payee,
                "category_id": category_id,
                "notes": notes,
            },
        )

    async def async_record_overtime(
        self,
        employment_id: int,
        work_date: date,
        hours: float,
        multiplier_bps: int | None = None,
        day_type: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """POST overtime entry — audited app-side (issue #102)."""
        return await self._post(
            f"/api/v1/employments/{employment_id}/overtime-entries",
            {
                "work_date": work_date.isoformat(),
                "hours": hours,
                "multiplier_bps": multiplier_bps,
                "day_type": day_type,
                "notes": notes,
            },
        )

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
        version = await self._get("/api/v1/version")
        await self._get("/api/v1/accounts")
        return version

    async def async_fetch_events(
        self, after: int = 0, *, latest: bool = False
    ) -> dict[str, Any]:
        return await self._get(
            f"/api/v1/integration/events?after={after}&limit=100"
            + ("&latest=true" if latest else "")
        )

    async def async_fetch_items(self) -> list[dict[str, Any]]:
        result = await self._get("/api/v1/integration/items?limit=100")
        return result["items"]

    async def async_energy_config(self) -> dict[str, Any]:
        return await self._get("/api/v1/energy/config")

    async def async_record_energy(self, day: date, wh: int) -> dict[str, Any]:
        return await self._post(
            "/api/v1/energy/readings",
            {"date": day.isoformat(), "wh": wh, "source": "ha_statistics"},
        )

    async def async_fetch_summary(self) -> dict[str, Any]:
        """Use real collection contracts; a missing route is a poll failure."""
        today = date.today()
        version = await self._get("/api/v1/version")
        accounts = await self._get("/api/v1/accounts")
        acct_rows = accounts["items"]
        total_balance = 0
        cursors = []
        connected_accounts = 0
        for account in acct_rows:
            balance = await self._get(f"/api/v1/accounts/{account['id']}/balance")
            total_balance += int(balance["balance_pence"])
            rows = await self._get(f"/api/v1/sync/accounts/{account['id']}/cursor")
            cursors.extend(rows)
            connected_accounts += bool(rows)
        states = {row["status"] for row in cursors}
        sync_status = next(
            (
                status
                for status in ("failed", "running", "queued", "completed", "idle")
                if status in states
            ),
            "manual" if not cursors else "unknown",
        )
        window_end = today + timedelta(days=60)
        movements = await self._get(
            f"/api/v1/expected-movements?start={today}&end={window_end}"
        )
        bills = [m for m in movements if m["amount_pence"] < 0]
        paydays = sorted(
            m["expected_date"]
            for m in movements
            if m.get("source_type") in ("payroll_run", "employment")
            and m["amount_pence"] > 0
        )
        spend = remaining = variance = None
        budgets = await self._get("/api/v1/budgets")
        current = next(
            (
                b
                for b in budgets["items"]
                if b["status"] in ("approved", "locked")
                and b["start_date"] <= str(today) <= b["end_date"]
            ),
            None,
        )
        if current is not None:
            detail = await self._get(f"/api/v1/budgets/{current['id']}")
            actuals = await self._get(f"/api/v1/budgets/{current['id']}/actuals")
            plan = sum(
                line["effective_pence"]
                for line in detail["lines"]
                if line["side"] == "expense"
            )
            spend = actuals.get("spent_pence")
            remaining = plan - spend if spend is not None else None
            variance = remaining
        goals = await self._get("/api/v1/savings-goals")
        goal_rows = goals["items"]
        on_track = 0
        for goal in goal_rows:
            progress = await self._get(f"/api/v1/savings-goals/{goal['id']}/progress")
            on_track += bool(progress["on_track"])
        outstanding = await self._get("/api/v1/expected-movements/outstanding")
        rules = await self._get("/api/v1/notifications/rules")
        kind_by_rule = {rule["id"]: rule.get("kind") for rule in rules}
        events = await self._get("/api/v1/notifications/events")
        pending = [event for event in events if event.get("state") == "delivered"]
        return {
            "app_version": version["version"],
            "account_count": len(acct_rows),
            "total_balance_pence": total_balance,
            "connected_accounts": connected_accounts,
            "sync_status": sync_status,
            "upcoming_bills_count": len(bills),
            "next_payday": paydays[0] if paydays else None,
            "month_spend_pence": spend,
            "month_remaining_pence": remaining,
            "budget_variance_pence": variance,
            "goals_on_track": on_track,
            "goals_total": len(goal_rows),
            "outstanding_commitments": len(outstanding),
            "notifications_pending": len(pending),
            "payslips_pending_review": sum(
                1
                for event in pending
                if kind_by_rule.get(event.get("rule_id")) == "payslip_review"
            ),
        }
