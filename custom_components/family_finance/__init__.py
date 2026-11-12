"""Family Finance integration (issue #100)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FamilyFinanceClient
from .const import DOMAIN, PLATFORMS
from .coordinator import FamilyFinanceCoordinator
from .services import async_register_services


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Create the coordinator, first-refresh, forward platforms."""
    client = FamilyFinanceClient(async_get_clientsession(hass), dict(entry.data))
    coordinator = FamilyFinanceCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    async_register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # First refresh precedes entity registration. Publish that pending batch
    # only after all enabled platforms are ready, then acknowledge it.
    coordinator.events_ready = True
    coordinator.async_update_listeners()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None and coordinator.options_snapshot != dict(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)
