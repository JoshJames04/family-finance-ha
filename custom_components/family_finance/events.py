"""Automation-hook events fired on coordinator state transitions
(issue #102).

Payloads are SANITISED: counts, flags and enum-style fields only —
never payees, notes, or individual amounts (same privacy contract as
the opt-in sensors).

- ``family_finance_bill_due``              — upcoming-bill count rose
- ``family_finance_bill_overdue``          — overdue-commitment count rose
- ``family_finance_sync_failed``           — a poll failed (once per streak)
- ``family_finance_goal_milestone``        — goals-on-track count rose
- ``family_finance_low_balance``           — total balance crossed below zero
- ``family_finance_notification_pending``  — unacknowledged events rose
- ``family_finance_payday``                — next-payday rolled forward (payday reached)
- ``family_finance_payslip_review``        — payslips awaiting review rose
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

DOMAIN_PREFIX = "family_finance_"
EVENT_BILL_DUE = f"{DOMAIN_PREFIX}bill_due"
EVENT_BILL_OVERDUE = f"{DOMAIN_PREFIX}bill_overdue"
EVENT_SYNC_FAILED = f"{DOMAIN_PREFIX}sync_failed"
EVENT_GOAL_MILESTONE = f"{DOMAIN_PREFIX}goal_milestone"
EVENT_LOW_BALANCE = f"{DOMAIN_PREFIX}low_balance"
EVENT_NOTIFICATION_PENDING = f"{DOMAIN_PREFIX}notification_pending"
EVENT_PAYDAY = f"{DOMAIN_PREFIX}payday"
EVENT_PAYSLIP_REVIEW = f"{DOMAIN_PREFIX}payslip_review"


def process_summary(
    hass: HomeAssistant,
    prev: dict[str, Any] | None,
    new: dict[str, Any],
    entry_id: str | None = None,
) -> None:
    """Compare consecutive poll payloads and fire transition events."""
    if prev is None:
        return
    # Compatibility helpers may omit scope; production coordinator always sets it.
    payload_scope = {"config_entry_id": entry_id} if entry_id is not None else {}

    bills_new = int(new.get("upcoming_bills_count") or 0)
    if bills_new > int(prev.get("upcoming_bills_count") or 0):
        hass.bus.async_fire(
            EVENT_BILL_DUE,
            {
                **payload_scope,
                "bills_due": bills_new,
                "window_days": 60,
            },
        )

    on_track_new = int(new.get("goals_on_track") or 0)
    if on_track_new > int(prev.get("goals_on_track") or 0):
        hass.bus.async_fire(
            EVENT_GOAL_MILESTONE,
            {
                **payload_scope,
                "goals_on_track": on_track_new,
                "goals_total": int(new.get("goals_total") or 0),
            },
        )

    balance_new = new.get("total_balance_pence")
    balance_prev = prev.get("total_balance_pence")
    if (
        balance_new is not None
        and int(balance_new) < 0
        and (balance_prev is None or int(balance_prev) >= 0)
    ):
        hass.bus.async_fire(
            EVENT_LOW_BALANCE,
            {
                **payload_scope,
                "account_count": int(new.get("account_count") or 0),
                "threshold_pence": 0,
            },
        )

    pending_new = int(new.get("notifications_pending") or 0)
    if pending_new > int(prev.get("notifications_pending") or 0):
        hass.bus.async_fire(
            EVENT_NOTIFICATION_PENDING,
            {
                **payload_scope,
                "pending_count": pending_new,
            },
        )

    overdue_new = int(new.get("outstanding_commitments") or 0)
    if overdue_new > int(prev.get("outstanding_commitments") or 0):
        hass.bus.async_fire(
            EVENT_BILL_OVERDUE,
            {
                **payload_scope,
                "overdue_count": overdue_new,
            },
        )

    payday_new = new.get("next_payday")
    payday_prev = prev.get("next_payday")
    if payday_new is not None and payday_prev is not None and payday_new > payday_prev:
        # The previously reported payday rolled to a later date —
        # that date was reached. Dates only; never amounts.
        hass.bus.async_fire(
            EVENT_PAYDAY,
            {
                **payload_scope,
                "reached": payday_prev,
                "next_payday": payday_new,
            },
        )

    review_new = int(new.get("payslips_pending_review") or 0)
    if review_new > int(prev.get("payslips_pending_review") or 0):
        hass.bus.async_fire(
            EVENT_PAYSLIP_REVIEW,
            {
                **payload_scope,
                "pending_count": review_new,
            },
        )


def process_failure(
    hass: HomeAssistant, already_failed: bool, entry_id: str | None = None
) -> None:
    """One ``sync_failed`` event per failure streak — the caller holds
    the flag so repeated failed polls don't spam the bus."""
    if not already_failed:
        hass.bus.async_fire(
            EVENT_SYNC_FAILED,
            {
                **({"config_entry_id": entry_id} if entry_id is not None else {}),
                "sync_status": "unreachable",
            },
        )
