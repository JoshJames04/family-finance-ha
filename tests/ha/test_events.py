"""Issue #102 — automation events: transitions, dedupe, sanitisation."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.family_finance.api import FamilyFinanceError
from custom_components.family_finance.const import (
    CONF_HOUSEHOLD_ID,
    CONF_URL,
    DOMAIN,
)
from custom_components.family_finance.events import (
    EVENT_BILL_DUE,
    EVENT_GOAL_MILESTONE,
    EVENT_LOW_BALANCE,
    EVENT_SYNC_FAILED,
    process_failure,
    process_summary,
)

_BASE = {
    "app_version": "0.1.0",
    "account_count": 2,
    "total_balance_pence": 50_000,
    "connected_accounts": 0,
    "sync_status": "manual",
    "upcoming_bills_count": 0,
    "next_payday": None,
    "month_spend_pence": 10_000,
    "month_remaining_pence": 40_000,
    "budget_variance_pence": 0,
    "goals_on_track": 0,
    "goals_total": 1,
    "outstanding_commitments": 0,
}

# every field any event payload may ever carry — sanitisation contract
_ALLOWED_KEYS = {
    "bills_due", "window_days", "goals_on_track", "goals_total",
    "account_count", "threshold_pence", "sync_status",
}


def _collect(hass: HomeAssistant, event: str) -> list[dict]:
    seen: list[dict] = []
    hass.bus.async_listen(event, lambda e: seen.append(dict(e.data)))
    return seen


class TestBillDue:
    async def test_rising_edge_fires(self, hass: HomeAssistant) -> None:
        seen = _collect(hass, EVENT_BILL_DUE)
        new = {**_BASE, "upcoming_bills_count": 3}
        process_summary(hass, _BASE, new)
        await hass.async_block_till_done()
        assert seen == [{"bills_due": 3, "window_days": 60}]

    async def test_no_rise_no_event(self, hass: HomeAssistant) -> None:
        seen = _collect(hass, EVENT_BILL_DUE)
        prev = {**_BASE, "upcoming_bills_count": 3}
        process_summary(hass, prev, dict(prev))
        process_summary(hass, prev,
                        {**_BASE, "upcoming_bills_count": 2})
        await hass.async_block_till_done()
        assert seen == []


class TestGoalMilestone:
    async def test_increase_fires(self, hass: HomeAssistant) -> None:
        seen = _collect(hass, EVENT_GOAL_MILESTONE)
        process_summary(hass, _BASE,
                        {**_BASE, "goals_on_track": 2})
        await hass.async_block_till_done()
        assert seen == [{"goals_on_track": 2, "goals_total": 1}]


class TestLowBalance:
    async def test_crossing_zero_fires_once(
            self, hass: HomeAssistant) -> None:
        seen = _collect(hass, EVENT_LOW_BALANCE)
        neg = {**_BASE, "total_balance_pence": -500}
        process_summary(hass, _BASE, neg)
        process_summary(hass, neg, neg)  # still negative → no refire
        await hass.async_block_till_done()
        assert len(seen) == 1

    async def test_recovery_then_re_cross_fires(
            self, hass: HomeAssistant) -> None:
        seen = _collect(hass, EVENT_LOW_BALANCE)
        neg = {**_BASE, "total_balance_pence": -500}
        process_summary(hass, _BASE, neg)
        process_summary(hass, neg, _BASE)
        process_summary(hass, _BASE, neg)
        await hass.async_block_till_done()
        assert len(seen) == 2


class TestSyncFailed:
    async def test_once_per_streak(self, hass: HomeAssistant) -> None:
        seen = _collect(hass, EVENT_SYNC_FAILED)
        process_failure(hass, already_failed=False)
        process_failure(hass, already_failed=True)
        await hass.async_block_till_done()
        assert seen == [{"sync_status": "unreachable"}]

    async def test_coordinator_fires_on_failure(
            self, hass: HomeAssistant, mock_api: AsyncMock) -> None:
        from homeassistant.helpers.update_coordinator import (
            UpdateFailed,
        )

        entry = MockConfigEntry(
            domain=DOMAIN, title="Family Finance",
            data={CONF_URL: "http://ff.local:8099",
                  CONF_HOUSEHOLD_ID: 1})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        seen = _collect(hass, EVENT_SYNC_FAILED)
        mock_api.side_effect = FamilyFinanceError("down")
        coord = hass.data[DOMAIN][entry.entry_id]
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()
        await hass.async_block_till_done()
        assert len(seen) == 1


class TestSanitisation:
    """Payloads carry counts/flags only — never payees or amounts."""

    async def test_payload_key_allowlist(
            self, hass: HomeAssistant) -> None:
        seen: list[dict] = []
        for ev in (EVENT_BILL_DUE, EVENT_GOAL_MILESTONE,
                   EVENT_LOW_BALANCE, EVENT_SYNC_FAILED):
            hass.bus.async_listen(
                ev, lambda e: seen.append(dict(e.data)))
        neg = {**_BASE, "total_balance_pence": -1,
               "upcoming_bills_count": 5, "goals_on_track": 3}
        process_summary(hass, _BASE, neg)
        process_failure(hass, already_failed=False)
        await hass.async_block_till_done()
        assert len(seen) == 4
        for payload in seen:
            assert set(payload) <= _ALLOWED_KEYS, payload
            for banned in ("payee", "amount_pence", "balance_pence",
                           "notes", "salary"):
                assert banned not in payload
