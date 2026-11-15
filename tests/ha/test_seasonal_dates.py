"""Explicit occasion reminders reach the existing Gifts event stream once."""

from unittest.mock import AsyncMock, patch

from homeassistant.core import callback
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.family_finance.const import DOMAIN


async def test_approaching_occasion_publishes_native_and_family_events(hass, mock_api):
    entry = MockConfigEntry(domain=DOMAIN, data={
        "url": "http://synthetic", "household_id": 1, "event_cursor": 4})
    entry.add_to_hass(hass)
    with patch("custom_components.family_finance.api.FamilyFinanceClient.async_fetch_events",
               new_callable=AsyncMock) as feed:
        feed.return_value = {"events": [], "next_cursor": 4}
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        seen = []

        @callback
        def received(event):
            seen.append(event.data)

        hass.bus.async_listen("family_finance_gifts_event", received)
        feed.return_value = {"events": [{
            "seq": 5, "event_type": "seasonal.approaching", "entity_kind": "seasonal_event",
            "entity_id": 7, "data": {"expected_date": "2026-12-20", "lead_days": 3},
        }], "next_cursor": 5}
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert len(seen) == 1 and seen[0]["event_type"] == "seasonal.approaching"
        assert seen[0]["expected_date"] == "2026-12-20"
        assert any(s.attributes.get("event_type") == "seasonal.approaching"
                   for s in hass.states.async_all("event"))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert len(seen) == 1
