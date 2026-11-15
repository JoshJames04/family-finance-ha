"""The Loans family delivers committed events once through native HA entities."""

from unittest.mock import AsyncMock, patch

from homeassistant.core import callback
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.family_finance.const import DOMAIN


async def test_loans_family_publishes_each_transition_without_replaying(hass, mock_api):
    entry = MockConfigEntry(domain=DOMAIN, data={
        "url": "http://synthetic", "household_id": 1, "event_cursor": 4})
    entry.add_to_hass(hass)
    with patch(
        "custom_components.family_finance.api.FamilyFinanceClient.async_fetch_events",
        new_callable=AsyncMock,
    ) as feed:
        feed.return_value = {"events": [], "next_cursor": 4}
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        seen = []

        @callback
        def received(event):
            seen.append(event.data)

        hass.bus.async_listen("family_finance_loans_event", received)
        feed.return_value = {"events": [
            {"seq": 5, "event_type": "loan.balance_changed", "entity_kind": "loan",
             "entity_id": 7, "data": {"status": "fully_repaid"}},
            {"seq": 6, "event_type": "loan.fully_repaid", "entity_kind": "loan",
             "entity_id": 7, "data": {"status": "fully_repaid"}},
        ], "next_cursor": 6}
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert [event["event_type"] for event in seen] == [
            "loan.balance_changed", "loan.fully_repaid"]
        assert all(event["entity_id"] == 7 for event in seen)
        assert any(state.attributes.get("event_type") == "loan.fully_repaid"
                   for state in hass.states.async_all("event"))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert len(seen) == 2
