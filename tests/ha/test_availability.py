"""Availability-transition tests (issue #100 AC): entities report
``unavailable`` within one poll cycle when the app goes down, and
recover on the next successful poll — never stale-wrong."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.family_finance.api import FamilyFinanceError
from custom_components.family_finance.const import (
    CONF_HOUSEHOLD_ID,
    CONF_URL,
    DOMAIN,
)

ENTITY = "sensor.family_finance_accounts"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, title="Family Finance",
        data={CONF_URL: "http://ff.local:8099", CONF_HOUSEHOLD_ID: 1})


async def test_setup_populates_sensor(
    hass: HomeAssistant, mock_api: AsyncMock,
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "3"
    assert state.attributes["app_version"] == "0.1.0"


async def test_app_down_marks_unavailable_in_one_cycle(
    hass: HomeAssistant, mock_api: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "3"  # type: ignore[union-attr]

    mock_api.side_effect = FamilyFinanceError("connection refused")
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_recovery_after_failure(
    hass: HomeAssistant, mock_api: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    mock_api.side_effect = FamilyFinanceError("down")
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE  # type: ignore[union-attr]

    mock_api.side_effect = None
    mock_api.return_value = {"app_version": "0.2.0", "account_count": 4}
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "4"
    assert state.attributes["app_version"] == "0.2.0"


async def test_first_refresh_failure_retries_not_crashes(
    hass: HomeAssistant, mock_api: AsyncMock,
) -> None:
    """App down at setup → ConfigEntryNotReady → retry later."""
    mock_api.side_effect = FamilyFinanceError("down")
    entry = _entry()
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state.name in ("SETUP_RETRY", "SETUP_ERROR")
