"""HA adapter publication of permitted rows; this does not grant private access."""
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.family_finance.const import DOMAIN


@pytest.mark.parametrize("event_type", ["overtime.recorded", "payroll.estimate_changed"])
async def test_payroll_change_stream_publishes_committed_rows_once(hass, mock_api, event_type):
    entry = MockConfigEntry(domain=DOMAIN, data={
        "url": "http://synthetic", "household_id": 1, "event_cursor": 4})
    entry.add_to_hass(hass)
    with patch("custom_components.family_finance.api.FamilyFinanceClient.async_fetch_events",
               new_callable=AsyncMock) as feed:
        feed.return_value = {"events": [], "next_cursor": 4}
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        seen = []
        hass.bus.async_listen("family_finance_payroll_event",
                              lambda event: seen.append(dict(event.data)))
        feed.return_value = {"events": [{
            "seq": 5, "event_type": event_type, "entity_kind": "employment", "entity_id": 7,
            "data": {"status": "estimate", "expected_date": "2026-01-28"},
        }], "next_cursor": 5}
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert len(seen) == 1
        assert seen[0]["event_type"] == event_type
        assert seen[0]["entity_id"] == 7
        assert entry.data["event_cursor"] == 5
        assert any(state.attributes.get("event_type") == event_type
                   for state in hass.states.async_all("event"))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert len(seen) == 1
