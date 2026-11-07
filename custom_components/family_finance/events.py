"""Automation-hook events fired on coordinator state transitions
(issue #102).

Payloads are SANITISED: counts, flags and enum-style fields only —
never payees, notes, or individual amounts (same privacy contract as
the opt-in sensors).

- ``family_finance_bill_due``       — upcoming-bill count rose
- ``family_finance_sync_failed``    — a poll failed (once per streak)
- ``family_finance_goal_milestone`` — goals-on-track count rose
- ``family_finance_low_balance``    — total balance crossed below zero
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

DOMAIN_PREFIX = "family_finance_"
EVENT_BILL_DUE = f"{DOMAIN_PREFIX}bill_due"
EVENT_SYNC_FAILED = f"{DOMAIN_PREFIX}sync_failed"
EVENT_GOAL_MILESTONE = f"{DOMAIN_PREFIX}goal_milestone"
EVENT_LOW_BALANCE = f"{DOMAIN_PREFIX}low_balance"


def process_summary(
    hass: HomeAssistant, prev: dict[str, Any] | None,
    new: dict[str, Any],
) -> None:
    """Compare consecutive poll payloads and fire transition events."""
    prev = prev or {}

    bills_new = int(new.get("upcoming_bills_count") or 0)
    if bills_new > int(prev.get("upcoming_bills_count") or 0):
        hass.bus.async_fire(EVENT_BILL_DUE, {
            "bills_due": bills_new,
            "window_days": 60,
        })

    on_track_new = int(new.get("goals_on_track") or 0)
    if on_track_new > int(prev.get("goals_on_track") or 0):
        hass.bus.async_fire(EVENT_GOAL_MILESTONE, {
            "goals_on_track": on_track_new,
            "goals_total": int(new.get("goals_total") or 0),
        })

    balance_new = new.get("total_balance_pence")
    balance_prev = prev.get("total_balance_pence")
    if (balance_new is not None and int(balance_new) < 0
            and (balance_prev is None or int(balance_prev) >= 0)):
        hass.bus.async_fire(EVENT_LOW_BALANCE, {
            "account_count": int(new.get("account_count") or 0),
            "threshold_pence": 0,
        })


def process_failure(hass: HomeAssistant, already_failed: bool) -> None:
    """One ``sync_failed`` event per failure streak — the caller holds
    the flag so repeated failed polls don't spam the bus."""
    if not already_failed:
        hass.bus.async_fire(EVENT_SYNC_FAILED, {
            "sync_status": "unreachable"})
