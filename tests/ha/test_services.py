"""Issue #102 — HA services round-trip + error surfacing."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from voluptuous import Invalid

from custom_components.family_finance.api import (
    FamilyFinanceAuthError,
    FamilyFinanceError,
)
from custom_components.family_finance.const import (
    CONF_HOUSEHOLD_ID,
    CONF_URL,
    DOMAIN,
)


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


class TestAddTransaction:
    async def test_happy_path_posts_and_refreshes(
            self, hass: HomeAssistant, mock_api: AsyncMock) -> None:
        await _setup(hass)
        with patch(
            "custom_components.family_finance.api.FamilyFinanceClient"
            ".async_add_transaction", new_callable=AsyncMock,
            return_value={"id": 7}) as post:
            await hass.services.async_call(
                DOMAIN, "add_transaction", {
                    "account_id": 2, "date": "2026-03-10",
                    "amount": -42.50, "payee": "Shops"},
                blocking=True)
            post.assert_awaited_once()
            kw = post.await_args.kwargs
            assert kw["account_id"] == 2
            assert kw["amount_pence"] == -4250
            assert kw["payee"] == "Shops"

    async def test_zero_amount_rejected(
            self, hass: HomeAssistant, mock_api: AsyncMock) -> None:
        await _setup(hass)
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "add_transaction", {
                    "account_id": 2, "date": "2026-03-10",
                    "amount": 0},
                blocking=True)

    async def test_api_error_surfaces_as_ha_error(
            self, hass: HomeAssistant, mock_api: AsyncMock) -> None:
        await _setup(hass)
        with patch(
            "custom_components.family_finance.api.FamilyFinanceClient"
            ".async_add_transaction", new_callable=AsyncMock,
            side_effect=FamilyFinanceError(
                "HTTP 422: bad account", status=422)), pytest.raises(
                    HomeAssistantError) as err:
            await hass.services.async_call(
                DOMAIN, "add_transaction", {
                    "account_id": 2, "date": "2026-03-10",
                    "amount": -10},
                blocking=True)
        assert "request failed" in str(err.value)

    async def test_auth_error_mentions_credentials(
            self, hass: HomeAssistant, mock_api: AsyncMock) -> None:
        await _setup(hass)
        with patch(
            "custom_components.family_finance.api.FamilyFinanceClient"
            ".async_add_transaction", new_callable=AsyncMock,
            side_effect=FamilyFinanceAuthError(
                "auth rejected", status=401)), pytest.raises(
                    HomeAssistantError) as err:
            await hass.services.async_call(
                DOMAIN, "add_transaction", {
                    "account_id": 2, "date": "2026-03-10",
                    "amount": -10},
                blocking=True)
        assert "credentials" in str(err.value)


class TestRecordOvertime:
    async def test_posts_entry(
            self, hass: HomeAssistant, mock_api: AsyncMock) -> None:
        await _setup(hass)
        with patch(
            "custom_components.family_finance.api.FamilyFinanceClient"
            ".async_record_overtime", new_callable=AsyncMock,
            return_value={"id": 3}) as post:
            await hass.services.async_call(
                DOMAIN, "record_overtime", {
                    "employment_id": 5, "work_date": "2026-03-09",
                    "hours": 2.5, "day_type": "saturday"},
                blocking=True)
            kw = post.await_args.kwargs
            assert kw["employment_id"] == 5
            assert kw["hours"] == 2.5
            assert kw["day_type"] == "saturday"

    async def test_bad_day_type_rejected(
            self, hass: HomeAssistant, mock_api: AsyncMock) -> None:
        await _setup(hass)
        with pytest.raises(Invalid):
            await hass.services.async_call(
                DOMAIN, "record_overtime", {
                    "employment_id": 5, "work_date": "2026-03-09",
                    "hours": 2, "day_type": "nonsense"},
                blocking=True)


class TestRefreshBankSync:
    async def test_refresh_success(
            self, hass: HomeAssistant, mock_api: AsyncMock) -> None:
        await _setup(hass)
        await hass.services.async_call(
            DOMAIN, "refresh_bank_sync", {}, blocking=True)

    async def test_refresh_failure_raises(
            self, hass: HomeAssistant, mock_api: AsyncMock) -> None:
        await _setup(hass)
        mock_api.side_effect = FamilyFinanceError("down")
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN, "refresh_bank_sync", {}, blocking=True)
