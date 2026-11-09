"""Sensor set contract + privacy tests (issue #101).

- Default config exposes NO monetary-value entity (opt-in per sensor).
- Every sensor maps to a documented coordinator field; the set is
  pinned so the API surface can't drift silently.
- States/attributes carry no payees or per-record amounts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.family_finance.const import (
    CONF_HOUSEHOLD_ID,
    CONF_URL,
    DOMAIN,
)
from custom_components.family_finance.sensor import SENSORS

COORDINATOR_FIELDS = {
    "app_version", "account_count", "total_balance_pence",
    "connected_accounts", "sync_status", "upcoming_bills_count",
    "next_payday", "month_spend_pence", "month_remaining_pence",
    "budget_variance_pence", "goals_on_track", "goals_total",
    "outstanding_commitments",
    "notifications_pending", "payslips_pending_review",
}


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, title="Family Finance",
        data={CONF_URL: "http://ff.local:8099", CONF_HOUSEHOLD_ID: 1})


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


class TestContract:
    def test_every_sensor_maps_to_documented_field(self) -> None:
        """The API↔sensor contract is pinned: no unmapped sensor and
        no documented field silently dropped from coverage."""
        mapped = {d.api_field for d in SENSORS}
        assert mapped <= COORDINATOR_FIELDS
        # Every *monetary* field has a sensor (none hidden).
        monetary = {f for f in COORDINATOR_FIELDS
                    if f.endswith("_pence")}
        assert monetary <= mapped

    def test_sensor_set_pinned(self) -> None:
        keys = {d.key for d in SENSORS}
        assert keys == {
            "accounts", "sync_status", "upcoming_bills",
            "next_payday", "goals_on_track",
            "outstanding_commitments", "total_balance",
            "month_spend", "month_remaining", "budget_variance",
            "notifications_pending", "payslips_pending_review",
        }


class TestPrivacyDefaults:
    def test_sensitive_descriptions_disabled_by_default(self) -> None:
        for d in SENSORS:
            assert d.sensitive == (
                d.device_class == SensorDeviceClass.MONETARY), (
                f"privacy tier mismatch on {d.key}")

    async def test_no_monetary_entity_enabled_by_default(
        self, hass: HomeAssistant, mock_api: AsyncMock,
    ) -> None:
        await _setup(hass)
        registry = er.async_get(hass)
        for entity in registry.entities.values():
            if entity.platform != DOMAIN:
                continue
            state = hass.states.get(entity.entity_id)
            if entity.disabled_by is None:
                assert state is not None
                assert state.attributes.get("device_class") != (
                    SensorDeviceClass.MONETARY.value), (
                    f"{entity.entity_id} exposes money by default")

    async def test_sensitive_entities_present_but_disabled(
        self, hass: HomeAssistant, mock_api: AsyncMock,
    ) -> None:
        await _setup(hass)
        registry = er.async_get(hass)
        monetary = [
            e for e in registry.entities.values()
            if e.platform == DOMAIN
            and e.disabled_by == er.RegistryEntryDisabler.INTEGRATION]
        assert len(monetary) == 4  # balance, spend, remaining, variance

    async def test_attributes_carry_no_sensitive_detail(
        self, hass: HomeAssistant, mock_api: AsyncMock,
    ) -> None:
        await _setup(hass)
        for state in hass.states.async_all("sensor"):
            if not state.entity_id.endswith((
                    "accounts", "sync_status", "upcoming_bills",
                    "next_payday", "goals_on_track",
                    "outstanding_commitments")):
                continue
            for attr in state.attributes.values():
                assert not isinstance(attr, (list, dict)), (
                    f"{state.entity_id} leaks structured data")


class TestValues:
    async def test_non_monetary_states(
        self, hass: HomeAssistant, mock_api: AsyncMock,
    ) -> None:
        await _setup(hass)
        assert hass.states.get(
            "sensor.family_finance_accounts").state == "3"  # type: ignore[union-attr]
        assert hass.states.get(
            "sensor.family_finance_upcoming_bills").state == "4"  # type: ignore[union-attr]
        assert hass.states.get(
            "sensor.family_finance_next_payday"
        ).state == "2026-11-27"  # type: ignore[union-attr]
        assert hass.states.get(
            "sensor.family_finance_sync_status"
        ).state == "manual"  # type: ignore[union-attr]

    async def test_monetary_conversion_pence_to_gbp(
        self, hass: HomeAssistant, mock_api: AsyncMock,
    ) -> None:
        """A monetary sensor converts pence → £ exactly."""
        from custom_components.family_finance.sensor import (
            FamilyFinanceSensor,
        )
        entry = await _setup(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]
        desc = next(d for d in SENSORS if d.key == "total_balance")
        sensor = FamilyFinanceSensor(coordinator, entry, desc)
        assert sensor.native_value == 1523.40
