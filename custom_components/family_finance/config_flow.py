"""UI config flow — app URL + optional token + household (issue #100)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import (
    async_get_clientsession,
)

from .api import FamilyFinanceAuthError, FamilyFinanceClient
from .api import FamilyFinanceError
from .const import CONF_HOUSEHOLD_ID, CONF_TOKEN, CONF_URL, DOMAIN

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_URL): str,
    vol.Optional(CONF_TOKEN): str,
    vol.Required(CONF_HOUSEHOLD_ID): vol.Coerce(int),
})


class FamilyFinanceConfigFlow(
        config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one Family Finance household connection."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_URL]}#{user_input[CONF_HOUSEHOLD_ID]}")
            self._abort_if_unique_id_configured()
            client = FamilyFinanceClient(
                async_get_clientsession(self.hass), user_input)
            try:
                await client.async_validate()
            except FamilyFinanceAuthError:
                errors["base"] = "invalid_auth"
            except FamilyFinanceError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="Family Finance", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors)
