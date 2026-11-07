"""Financial sensors with privacy opt-ins (issue #101).

Privacy contract: every description maps to a documented coordinator
field (``api_field``), and any sensor whose state is a monetary value
is disabled by default — the household opts in per entity in the
entity registry. Attributes never carry payees, counterparties, or
per-record amounts beyond the state value itself. Recorder-exclusion
guidance lives in docs/PRIVACY.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_VERSION, DOMAIN
from .coordinator import FamilyFinanceCoordinator


@dataclass(frozen=True, kw_only=True)
class FamilyFinanceSensorDescription(SensorEntityDescription):
    api_field: str
    sensitive: bool = False


SENSORS: tuple[FamilyFinanceSensorDescription, ...] = (
    FamilyFinanceSensorDescription(
        key="accounts", name="Accounts",
        api_field="account_count", icon="mdi:bank"),
    FamilyFinanceSensorDescription(
        key="sync_status", name="Sync status",
        api_field="sync_status", icon="mdi:bank-sync",
        entity_category=EntityCategory.DIAGNOSTIC),
    FamilyFinanceSensorDescription(
        key="upcoming_bills", name="Upcoming bills",
        api_field="upcoming_bills_count", icon="mdi:calendar-clock"),
    FamilyFinanceSensorDescription(
        key="next_payday", name="Next payday",
        api_field="next_payday", icon="mdi:calendar-star"),
    FamilyFinanceSensorDescription(
        key="goals_on_track", name="Goals on track",
        api_field="goals_on_track", icon="mdi:target"),
    FamilyFinanceSensorDescription(
        key="outstanding_commitments",
        name="Outstanding commitments",
        api_field="outstanding_commitments",
        icon="mdi:clipboard-alert"),
    # Sensitive — monetary values, disabled until the household opts in.
    FamilyFinanceSensorDescription(
        key="total_balance", name="Total balance",
        api_field="total_balance_pence", icon="mdi:scale-balance",
        sensitive=True, device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="£"),
    FamilyFinanceSensorDescription(
        key="month_spend", name="Spend this month",
        api_field="month_spend_pence", icon="mdi:cash-minus",
        sensitive=True, device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="£"),
    FamilyFinanceSensorDescription(
        key="month_remaining", name="Remaining this month",
        api_field="month_remaining_pence", icon="mdi:cash-check",
        sensitive=True, device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="£"),
    FamilyFinanceSensorDescription(
        key="budget_variance", name="Budget variance",
        api_field="budget_variance_pence", icon="mdi:chart-line-variant",
        sensitive=True, device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="£"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FamilyFinanceCoordinator = (
        hass.data[DOMAIN][entry.entry_id])
    async_add_entities(
        FamilyFinanceSensor(coordinator, entry, d) for d in SENSORS)


class FamilyFinanceSensor(
        CoordinatorEntity[FamilyFinanceCoordinator], SensorEntity):
    """One described sensor; availability follows the coordinator."""

    entity_description: FamilyFinanceSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FamilyFinanceCoordinator,
        entry: ConfigEntry,
        description: FamilyFinanceSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_name = description.name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        # Monetary sensors start disabled — opt-in per entity (#101).
        self._attr_entity_registry_enabled_default = (
            not description.sensitive)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Family Finance",
        }

    @property
    def native_value(self) -> object:
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(
            self.entity_description.api_field)
        if value is None:
            return None
        if self.entity_description.device_class == \
                SensorDeviceClass.MONETARY:
            return int(value) / 100  # pence → GBP
        return value

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Only non-sensitive context — never payees or per-record
        amounts (issue #101)."""
        if self.coordinator.data is None:
            return {}
        attrs: dict[str, object] = {
            ATTR_VERSION: self.coordinator.data.get(
                "app_version", "unknown")}
        if (self.entity_description.key == "goals_on_track"
                and "goals_total" in self.coordinator.data):
            attrs["goals_total"] = self.coordinator.data["goals_total"]
        return attrs
