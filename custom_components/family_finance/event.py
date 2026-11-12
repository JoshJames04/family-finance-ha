"""Per-family EventEntity streams backed exclusively by committed outbox rows."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOUSEHOLD_ID, DOMAIN
from .coordinator import FamilyFinanceCoordinator
from .outbox import EVENT_TYPES, family_for


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        FamilyFinanceEvent(coordinator, entry, family) for family in EVENT_TYPES
    )


class FamilyFinanceEvent(CoordinatorEntity[FamilyFinanceCoordinator], EventEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FamilyFinanceCoordinator, entry: ConfigEntry, family: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._family = family
        self._last_seq = 0
        self._attr_name = f"{family.capitalize()} events"
        self._attr_unique_id = f"{entry.entry_id}_events_{family}"
        self._attr_event_types = EVENT_TYPES[family]
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Family Finance",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._publish()

    def _handle_coordinator_update(self) -> None:
        self._publish()
        super()._handle_coordinator_update()

    def _publish(self) -> None:
        for row in (self.coordinator.data or {}).get("events", []):
            seq = int(row["seq"])
            if seq <= self._last_seq or family_for(row["event_type"]) != self._family:
                continue
            # New canonical types can roll out independently of the integration.
            if row["event_type"] not in self._attr_event_types:
                self._attr_event_types = [*self._attr_event_types, row["event_type"]]
            payload: dict[str, Any] = {
                **row.get("data", {}),
                "schema_version": 1,
                "seq": seq,
                "entity_kind": row.get("entity_kind"),
                "entity_id": row.get("entity_id"),
                "occurred_at": row.get("occurred_at"),
                "config_entry_id": self._entry.entry_id,
                "household_id": self._entry.data[CONF_HOUSEHOLD_ID],
            }
            self._trigger_event(row["event_type"], payload)
            self.hass.bus.async_fire(
                f"{DOMAIN}_{self._family}_event",
                {**payload, "event_type": row["event_type"]},
            )
            self._last_seq = seq
