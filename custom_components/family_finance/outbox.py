"""Durable outbox cursor policy; no finance logic or aggregate inference."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

FAMILY_PREFIXES = {
    "movement": "bills",
    "transaction": "banking",
    "account": "banking",
    "budget": "budgets",
    "subscription": "subscriptions",
    "goal": "goals",
    "payroll": "payroll",
    "payslip": "payroll",
    "gift": "gifts",
    "seasonal": "gifts",
    "energy": "energy",
    "backup": "ops",
    "data": "ops",
    "sync": "ops",
}
EVENT_TYPES = {
    "bills": [
        "movement.settled",
        "movement.partial",
        "movement.overdue",
    ],
    "banking": ["transaction.imported"],
    "budgets": [
        "budget.usage",
        "budget.over",
        "budget.remaining_changed",
        "budget.approved",
        "budget.locked",
    ],
    "subscriptions": ["subscription.price_observed"],
    "goals": ["goal.milestone"],
    "payroll": [
        "payroll.generated",
        "payroll.reconciled",
        "payroll.approaching",
        "payslip.review_required",
        "payslip.state_changed",
    ],
    "gifts": ["gift.purchased", "gift.paid", "seasonal.variance"],
    "energy": ["energy.variance", "energy.reading_updated"],
    "ops": [
        "sync.failed",
        "sync.recovered",
        "sync.completed",
        "backup.created",
        "backup.failed",
        "data.stale",
    ],
}


def family_for(event_type: str) -> str | None:
    return FAMILY_PREFIXES.get(event_type.partition(".")[0])


class EventDrain:
    """Initial install skips history; an existing cursor catches up in order.

    Cursor acknowledgement follows a validated page, including unknown future
    types. A failed request never advances it. Sequence IDs remain in payloads
    so external consumers can deduplicate a crash during HA's storage flush.
    """

    def __init__(
        self, client: Any, cursor: int | None, persist: Callable[[int], None]
    ) -> None:
        self.client = client
        self.cursor = cursor
        self.persist = persist

    async def poll(self) -> list[dict[str, Any]]:
        if self.cursor is None:
            result = await self.client.async_fetch_events(latest=True)
            self.cursor = int(result["next_cursor"])
            self.persist(self.cursor)
            return []
        events: list[dict[str, Any]] = []
        cursor = self.cursor
        # Bounded catch-up: at most 1000 events per coordinator refresh.
        for _ in range(10):
            result = await self.client.async_fetch_events(cursor)
            rows = result["events"]
            fresh = [row for row in rows if int(row["seq"]) > cursor]
            if fresh != sorted(fresh, key=lambda row: int(row["seq"])):
                raise ValueError("outbox sequence is not ordered")
            next_cursor = int(result["next_cursor"])
            if next_cursor < cursor or any(
                int(row["seq"]) > next_cursor for row in fresh
            ):
                raise ValueError("invalid outbox cursor")
            events.extend(fresh)
            cursor = next_cursor
            if len(rows) < 100:
                break
        self.cursor = cursor
        self.persist(cursor)
        return events
