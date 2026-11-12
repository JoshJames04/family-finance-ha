"""UI config flow — app URL + optional token + household (issue #100)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import (
    async_get_clientsession,
)

from .api import FamilyFinanceAuthError, FamilyFinanceClient, FamilyFinanceError
from .const import CONF_HOUSEHOLD_ID, CONF_TOKEN, CONF_URL, DOMAIN
from .items import ENTITY_CAP, ITEM_FAMILIES

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Optional(CONF_TOKEN): str,
        vol.Required(CONF_HOUSEHOLD_ID): vol.Coerce(int),
    }
)


class FamilyFinanceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one Family Finance household connection."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> FamilyFinanceOptionsFlow:
        return FamilyFinanceOptionsFlow()

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            client = FamilyFinanceClient(
                async_get_clientsession(self.hass),
                {**entry.data, CONF_TOKEN: user_input[CONF_TOKEN]},
            )
            try:
                await client.async_validate()
            except FamilyFinanceAuthError:
                errors["base"] = "invalid_auth"
            except FamilyFinanceError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_URL]}#{user_input[CONF_HOUSEHOLD_ID]}"
            )
            self._abort_if_unique_id_configured()
            client = FamilyFinanceClient(async_get_clientsession(self.hass), user_input)
            try:
                await client.async_validate()
            except FamilyFinanceAuthError:
                errors["base"] = "invalid_auth"
            except FamilyFinanceError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="Family Finance", data=user_input)
        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)


class FamilyFinanceOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        client = FamilyFinanceClient(
            async_get_clientsession(self.hass), dict(self.config_entry.data)
        )
        try:
            items = await client.async_fetch_items()
        except FamilyFinanceError:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({}),
                errors={"base": "cannot_connect"},
            )
        errors = {}
        if user_input is not None:
            count = 0
            for family in ITEM_FAMILIES:
                mode = user_input.get(f"{family}_mode", "none")
                ids = set(user_input.get(f"{family}_selected", []))
                count += sum(
                    1
                    for item in items
                    if item["family"] == family
                    and (mode == "all" or (mode == "selected" and str(item["id"]) in ids))
                )
            if count > int(user_input.get("entity_cap", ENTITY_CAP)):
                errors["base"] = "too_many_entities"
            else:
                return self.async_create_entry(title="", data=user_input)
        fields = {
            vol.Optional(
                "entity_cap",
                default=self.config_entry.options.get("entity_cap", ENTITY_CAP),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=ENTITY_CAP))
        }
        for family in ITEM_FAMILIES:
            fields[
                vol.Optional(
                    f"{family}_mode",
                    default=self.config_entry.options.get(f"{family}_mode", "none"),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["none", "all", "selected"],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            fields[
                vol.Optional(
                    f"{family}_selected",
                    default=self.config_entry.options.get(f"{family}_selected", []),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": str(item["id"]), "label": item["name"]}
                        for item in items
                        if item["family"] == family
                    ],
                    multiple=True,
                )
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields), errors=errors)
