"""Scaffold sensor — proves coordinator wiring + availability semantics.

The privacy-controlled financial sensor set lands with issue #101; this
keeps #100 minimal: one connectivity/status entity that honestly goes
unavailable when the app is down.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_VERSION, DOMAIN
from .coordinator import FamilyFinanceCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FamilyFinanceCoordinator = (
        hass.data[DOMAIN][entry.entry_id])
    async_add_entities([FamilyFinanceStatusSensor(coordinator, entry)])


class FamilyFinanceStatusSensor(
        CoordinatorEntity[FamilyFinanceCoordinator], SensorEntity):
    """Reports linked account count; unavailable whenever a poll fails
    (CoordinatorEntity drives ``available`` from
    ``last_update_success``)."""

    _attr_has_entity_name = True
    _attr_name = "Accounts"
    _attr_icon = "mdi:bank"

    def __init__(
        self, coordinator: FamilyFinanceCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_accounts"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Family Finance",
        }

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return int(self.coordinator.data.get("account_count", 0))

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        if self.coordinator.data is None:
            return {}
        return {ATTR_VERSION: str(
            self.coordinator.data.get("app_version", "unknown"))}
