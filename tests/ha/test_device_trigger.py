"""Issue #424 — native device triggers: discovery on our device,
isolation from foreign devices, and event wiring through the core
event trigger."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from custom_components.family_finance import device_trigger
from custom_components.family_finance.const import CONF_HOUSEHOLD_ID, CONF_URL, DOMAIN
from custom_components.family_finance.events import EVENT_BILL_OVERDUE, EVENT_PAYDAY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

_EXPECTED_TYPES = {
    "bill_due", "bill_overdue", "sync_failed", "goal_milestone",
    "low_balance", "notification_pending", "payday", "payslip_review",
}


async def _setup_device(hass: HomeAssistant) -> dr.DeviceEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Family Finance",
        data={CONF_URL: "http://ff.local:8099", CONF_HOUSEHOLD_ID: 1})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    return device


async def test_get_triggers_offers_all_events(
        hass: HomeAssistant, mock_api: AsyncMock) -> None:
    device = await _setup_device(hass)
    triggers = await device_trigger.async_get_triggers(hass, device.id)
    assert {t["type"] for t in triggers} == _EXPECTED_TYPES
    for t in triggers:
        assert t["platform"] == "device"
        assert t["domain"] == DOMAIN
        assert t["device_id"] == device.id


async def test_foreign_device_gets_nothing(
        hass: HomeAssistant, mock_api: AsyncMock) -> None:
    await _setup_device(hass)
    registry = dr.async_get(hass)
    foreign = registry.async_get_or_create(
        config_entry_id="other-entry",
        identifiers={("other_domain", "x")})
    assert await device_trigger.async_get_triggers(
        hass, foreign.id) == []
    assert await device_trigger.async_get_triggers(
        hass, "no-such-device") == []


async def test_attach_fires_mapped_event(
        hass: HomeAssistant, mock_api: AsyncMock) -> None:
    device = await _setup_device(hass)
    triggers = await device_trigger.async_get_triggers(hass, device.id)
    config = next(t for t in triggers if t["type"] == "bill_overdue")
    action = AsyncMock()
    remove = await device_trigger.async_attach_trigger(
        hass, config, action,
        {"domain": "automation", "name": "test",
         "home_assistant_start": False, "variables": {},
         "trigger_data": {}})
    hass.bus.async_fire(EVENT_BILL_OVERDUE, {"overdue_count": 2})
    await hass.async_block_till_done()
    action.assert_awaited_once()
    remove()   # teardown: unsubscribes cleanly


async def test_attach_payday_fires(
        hass: HomeAssistant, mock_api: AsyncMock) -> None:
    device = await _setup_device(hass)
    config = {"platform": "device", "domain": DOMAIN,
              "device_id": device.id, "type": "payday"}
    action = AsyncMock()
    await device_trigger.async_attach_trigger(
        hass, config, action,
        {"domain": "automation", "name": "test",
         "home_assistant_start": False, "variables": {},
         "trigger_data": {}})
    hass.bus.async_fire(EVENT_PAYDAY, {
        "reached": "2026-11-14", "next_payday": "2026-11-28"})
    await hass.async_block_till_done()
    action.assert_awaited_once()


async def test_schema_rejects_unknown_type() -> None:
    import voluptuous as vol
    with pytest.raises(vol.Invalid):
        device_trigger.TRIGGER_SCHEMA({
            "platform": "device", "domain": DOMAIN,
            "device_id": "x", "type": "not_a_trigger"})
