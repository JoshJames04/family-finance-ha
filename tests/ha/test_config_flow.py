"""Config-flow tests (issue #100): UI-driven setup, error mapping,
duplicate prevention."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.family_finance.api import (
    FamilyFinanceAuthError,
    FamilyFinanceError,
)
from custom_components.family_finance.const import (
    CONF_URL,
    DOMAIN,
)

VALID = {
    "url": "http://family-finance.local:8099",
    "household_id": 1,
}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_form_shown(hass: HomeAssistant) -> None:
    result = await _start(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_entry_created_on_valid_connection(
    hass: HomeAssistant, mock_api: AsyncMock,
) -> None:
    result = await _start(hass)
    with patch(
        "custom_components.family_finance.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], VALID)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == VALID[CONF_URL]
    mock_api.validate.assert_awaited_once()  # type: ignore[attr-defined]


async def test_cannot_connect_maps_to_form_error(
    hass: HomeAssistant, mock_api: AsyncMock,
) -> None:
    mock_api.validate.side_effect = FamilyFinanceError("down")  # type: ignore[attr-defined]
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_invalid_auth_maps_to_form_error(
    hass: HomeAssistant, mock_api: AsyncMock,
) -> None:
    mock_api.validate.side_effect = FamilyFinanceAuthError("401")  # type: ignore[attr-defined]
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_same_household_twice_aborts(
    hass: HomeAssistant, mock_api: AsyncMock,
) -> None:
    result = await _start(hass)
    with patch(
        "custom_components.family_finance.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], VALID)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    again = await _start(hass)
    again = await hass.config_entries.flow.async_configure(
        again["flow_id"], VALID)
    assert again["type"] == FlowResultType.ABORT
    assert again["reason"] == "already_configured"
