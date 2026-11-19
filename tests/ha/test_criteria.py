"""Expanded numeric families remain native, opt-in, bounded and discoverable."""

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.family_finance.api import FamilyFinanceClient, FamilyFinanceError
from custom_components.family_finance.const import DOMAIN


async def test_numeric_criteria_offer_named_all_selected_with_shared_entity_cap(hass, mock_api):
    entry = MockConfigEntry(domain=DOMAIN, data={"url": "http://synthetic", "household_id": 1})
    entry.add_to_hass(hass)
    items = [
        {"family": "budget_remaining", "id": "budget:17", "name": "Synthetic plan remaining",
         "value": "75.00", "unit": "GBP"},
        {"family": "seasonal_usage", "id": "seasonal:21", "name": "Synthetic season usage",
         "value": "25.00", "unit": "%"},
    ]
    with patch("custom_components.family_finance.api.FamilyFinanceClient.async_fetch_items",
               new_callable=AsyncMock, return_value=items):
        form = await hass.config_entries.options.async_init(entry.entry_id)
        fields = {str(key) for key in form["data_schema"].schema}
        assert {"budget_remaining_mode", "seasonal_usage_mode", "paydays_mode", "forecast_days"} <= fields
        chosen = {"entity_cap": 1, "forecast_days": 7, "budget_remaining_mode": "selected",
                  "budget_remaining_selected": ["budget:17"], "seasonal_usage_mode": "all"}
        result = await hass.config_entries.options.async_configure(form["flow_id"], chosen)
        assert result["errors"] == {"base": "too_many_entities"}
        result = await hass.config_entries.options.async_configure(
            form["flow_id"], {**chosen, "entity_cap": 2})
        assert result["type"].value == "create_entry"
        assert entry.options["budget_remaining_selected"] == ["budget:17"]
        assert entry.options["paydays_mode"] == "none"
        assert entry.options["forecast_days"] == 7


async def test_discovery_drains_pages_so_later_families_remain_selectable():
    client = FamilyFinanceClient(AsyncMock(), {"url": "http://synthetic", "household_id": 1})
    first = {"family": "accounts", "id": "account:1"}
    later = {"family": "budget_remaining", "id": "budget:2"}
    with patch.object(client, "_get", new_callable=AsyncMock, side_effect=[
        {"items": [first], "has_more": True}, {"items": [later], "has_more": False},
    ]):
        assert await client.async_fetch_items() == [first, later]


async def test_discovery_over_limit_fails_closed_instead_of_silently_truncating():
    client = FamilyFinanceClient(AsyncMock(), {"url": "http://synthetic", "household_id": 1})
    with patch.object(client, "_get", new_callable=AsyncMock,
                      return_value={"items": [], "has_more": True}):
        with pytest.raises(FamilyFinanceError, match="discovery"):
            await client.async_fetch_items()


@pytest.mark.parametrize("family,value,unit", [
    ("safe_to_spend", "45.00", "GBP"), ("budget_usage", "25.00", "%"), ("paydays", 7, "d"),
])
async def test_new_metric_units_and_identity_survive_rename_and_become_unavailable(
        hass, mock_api, family, value, unit):
    entry = MockConfigEntry(domain=DOMAIN, data={"url": "http://synthetic", "household_id": 1},
                            options={f"{family}_mode": "selected", f"{family}_selected": ["item:7"]})
    entry.add_to_hass(hass)
    item = {"family": family, "id": "item:7", "name": "Synthetic metric", "value": value, "unit": unit}
    with patch("custom_components.family_finance.api.FamilyFinanceClient.async_fetch_items",
               new_callable=AsyncMock, return_value=[item]) as fetch:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        state = next(state for state in hass.states.async_all("sensor")
                     if state.attributes.get("friendly_name", "").endswith("Synthetic metric"))
        assert float(state.state) == float(value)
        assert state.attributes["unit_of_measurement"] == unit
        fetch.return_value = [{**item, "name": "Renamed metric"}]
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(state.entity_id).attributes["friendly_name"].endswith("Renamed metric")
        fetch.return_value = []
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(state.entity_id).state == "unavailable"
