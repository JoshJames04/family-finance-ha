"""Outbox event entities and stable per-item numeric-state contracts."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant, callback
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.family_finance.const import DOMAIN


async def test_failed_publication_keeps_cursor_until_consumers_succeed(hass, mock_api):
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
        coordinator = hass.data[DOMAIN][entry.entry_id]
        feed.return_value = {"events": [{
            "seq": 5, "event_type": "movement.settled", "data": {},
        }], "next_cursor": 5}
        batch = await coordinator._async_update_data()
        assert entry.data["event_cursor"] == 4
        with (
            patch("custom_components.family_finance.event.FamilyFinanceEvent._publish",
                  side_effect=RuntimeError("Synthetic publisher failure")),
            pytest.raises(RuntimeError, match="Synthetic publisher failure"),
        ):
            coordinator.async_set_updated_data(batch)
        assert entry.data["event_cursor"] == 4
        assert coordinator.drain.cursor == 4
        coordinator.async_set_updated_data(batch)
        await hass.async_block_till_done()
        assert entry.data["event_cursor"] == 5
        assert any(state.attributes.get("seq") == 5 for state in hass.states.async_all("event"))


async def test_outbox_entities_skip_initial_history_and_emit_committed_rows(
    hass: HomeAssistant, mock_api
):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://synthetic", "household_id": 1}
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.family_finance.api.FamilyFinanceClient.async_fetch_events",
        new_callable=AsyncMock,
    ) as feed:
        feed.return_value = {"events": [], "next_cursor": 4}
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.data["event_cursor"] == 4
        coordinator = hass.data[DOMAIN][entry.entry_id]
        seen = []
        hass.bus.async_listen(
            "family_finance_bills_event", lambda event: seen.append(dict(event.data))
        )
        feed.return_value = {
            "events": [
                {
                    "seq": 5,
                    "event_type": "movement.settled",
                    "entity_kind": "expected_movement",
                    "entity_id": 7,
                    "occurred_at": "2026-11-12T12:00:00",
                    "data": {"status": "satisfied"},
                }
            ],
            "next_cursor": 5,
        }
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert len(seen) == 1
        assert seen[0]["seq"] == 5
        assert seen[0]["config_entry_id"] == entry.entry_id
        assert entry.data["event_cursor"] == 5
        await coordinator.async_refresh()  # repeated/overlapping feed is harmless
        await hass.async_block_till_done()
        assert len(seen) == 1
        assert any(
            state.attributes.get("event_type") == "movement.settled"
            for state in hass.states.async_all("event")
        )


async def test_per_item_sensor_opt_in_numeric_and_stable_identity(hass, mock_api):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"url": "http://synthetic", "household_id": 1},
        options={"accounts_mode": "selected", "accounts_selected": ["17"]},
    )
    entry.add_to_hass(hass)
    item = {
        "family": "accounts",
        "id": "17",
        "name": "Synthetic account",
        "value": 12.34,
        "unit": "GBP",
    }
    with patch(
        "custom_components.family_finance.api.FamilyFinanceClient.async_fetch_items",
        new_callable=AsyncMock,
        return_value=[item],
    ) as fetch:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        states = [
            state
            for state in hass.states.async_all("sensor")
            if state.attributes.get("friendly_name", "").endswith("Synthetic account")
        ]
        assert len(states) == 1
        assert states[0].state == "12.34"
        entity_id = states[0].entity_id
        fetch.return_value = [{**item, "name": "Renamed account", "value": "20.00"}]
        await hass.data[DOMAIN][entry.entry_id].async_refresh()
        await hass.async_block_till_done()
        assert float(hass.states.get(entity_id).state) == 20
        assert (
            hass.states.get(entity_id)
            .attributes["friendly_name"]
            .endswith("Renamed account")
        )


async def test_each_event_in_one_poll_publishes_a_native_state_change(hass, mock_api):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://synthetic", "household_id": 1}
    )
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
        def changed(event):
            state = event.data.get("new_state")
            if (
                state
                and state.entity_id.startswith("event.")
                and state.attributes.get("seq") in {5, 6}
            ):
                seen.append(state.attributes["seq"])

        hass.bus.async_listen("state_changed", changed)
        feed.return_value = {
            "events": [
                {
                    "seq": seq,
                    "event_type": "transaction.imported",
                    "entity_kind": "transaction",
                    "entity_id": seq,
                    "occurred_at": "2026-11-12T12:00:00+00:00",
                    "data": {},
                }
                for seq in (5, 6)
            ],
            "next_cursor": 6,
        }
        await hass.data[DOMAIN][entry.entry_id].async_refresh()
        await hass.async_block_till_done()
        assert seen == [5, 6]


async def test_options_all_selected_are_visual_and_reject_over_cap(hass, mock_api):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://synthetic", "household_id": 1}
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.family_finance.api.FamilyFinanceClient.async_fetch_items",
        new_callable=AsyncMock,
        return_value=[
            {
                "family": "accounts",
                "id": "17",
                "name": "Account A",
                "value": 12,
                "unit": "GBP",
            },
            {"family": "goals", "id": "21", "name": "Goal B", "value": 50, "unit": "%"},
        ],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"].value == "form"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "entity_cap": 1,
                "accounts_mode": "all",
                "accounts_selected": [],
                "bills_mode": "none",
                "bills_selected": [],
                "goals_mode": "all",
                "goals_selected": [],
            },
        )
        assert result["errors"] == {"base": "too_many_entities"}
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "entity_cap": 2,
                "accounts_mode": "selected",
                "accounts_selected": ["17"],
                "bills_mode": "none",
                "bills_selected": [],
                "goals_mode": "all",
                "goals_selected": [],
            },
        )
        assert result["type"].value == "create_entry"
        assert entry.options["accounts_selected"] == ["17"]


async def test_reload_baseline_does_not_replay_summary_history(hass, mock_api):
    from custom_components.family_finance.events import EVENT_BILL_DUE

    entry = MockConfigEntry(
        domain=DOMAIN, data={"url": "http://synthetic", "household_id": 1}
    )
    entry.add_to_hass(hass)
    seen = []
    hass.bus.async_listen(EVENT_BILL_DUE, lambda event: seen.append(event))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert seen == []
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert seen == []
