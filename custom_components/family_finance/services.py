"""HA services: write-through actions + data refresh (issue #102).

Every service validates inputs, surfaces failures as actionable HA
errors (never silent drops), and relies on the app's own audit trail —
each POST lands an audited record app-side.
"""

from __future__ import annotations

from datetime import date, datetime

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv

from .api import FamilyFinanceAuthError, FamilyFinanceError
from .const import DOMAIN
from .coordinator import FamilyFinanceCoordinator

SERVICE_ADD_TRANSACTION = "add_transaction"
SERVICE_RECORD_OVERTIME = "record_overtime"
SERVICE_REFRESH_BANK_SYNC = "refresh_bank_sync"

_SCHEMA_ADD_TRANSACTION = vol.Schema({
    vol.Required("account_id"): cv.positive_int,
    vol.Required("date"): cv.date,
    vol.Required("amount"): vol.Coerce(float),
    vol.Optional("payee", default="Home Assistant"): vol.All(
        str, vol.Length(min=1, max=200)),
    vol.Optional("category_id"): cv.positive_int,
    vol.Optional("notes"): vol.All(str, vol.Length(max=2000)),
})

_SCHEMA_RECORD_OVERTIME = vol.Schema({
    vol.Required("employment_id"): cv.positive_int,
    vol.Required("work_date"): cv.date,
    vol.Required("hours"): vol.All(
        vol.Coerce(float), vol.Range(min=0, max=24,
                                     min_included=False)),
    vol.Optional("multiplier_bps"): vol.All(
        cv.positive_int, vol.Range(max=100_000)),
    vol.Optional("day_type"): vol.In(
        ["weekday", "saturday", "sunday", "bank_holiday"]),
    vol.Optional("notes"): vol.All(str, vol.Length(max=2000)),
})


def _coordinator(hass: HomeAssistant) -> FamilyFinanceCoordinator:
    coords = list(hass.data.get(DOMAIN, {}).values())
    if not coords:
        raise HomeAssistantError("Family Finance is not configured")
    return coords[0]


def _raise_ha(exc: FamilyFinanceError) -> HomeAssistantError:
    if isinstance(exc, FamilyFinanceAuthError):
        return HomeAssistantError(
            "Family Finance credentials rejected — check the "
            "integration's token")
    return HomeAssistantError(
        f"Family Finance request failed: {exc}")


def async_register_services(hass: HomeAssistant) -> None:
    """Idempotent registration — services are domain-level."""

    async def add_transaction(call: ServiceCall) -> None:
        coord = _coordinator(hass)
        d = call.data
        amount_pence = round(float(d["amount"]) * 100)
        if amount_pence == 0:
            raise ServiceValidationError("amount must be non-zero")
        txn_date: date = (
            d["date"] if isinstance(d["date"], date)
            else datetime.strptime(str(d["date"]), "%Y-%m-%d").date())
        try:
            await coord.client.async_add_transaction(
                account_id=d["account_id"], txn_date=txn_date,
                amount_pence=amount_pence, payee=d["payee"],
                category_id=d.get("category_id"),
                notes=d.get("notes"))
        except FamilyFinanceError as exc:
            raise _raise_ha(exc) from exc
        await coord.async_request_refresh()

    async def record_overtime(call: ServiceCall) -> None:
        coord = _coordinator(hass)
        d = call.data
        work_date: date = (
            d["work_date"] if isinstance(d["work_date"], date)
            else datetime.strptime(
                str(d["work_date"]), "%Y-%m-%d").date())
        try:
            await coord.client.async_record_overtime(
                employment_id=d["employment_id"], work_date=work_date,
                hours=float(d["hours"]),
                multiplier_bps=d.get("multiplier_bps"),
                day_type=d.get("day_type"), notes=d.get("notes"))
        except FamilyFinanceError as exc:
            raise _raise_ha(exc) from exc
        await coord.async_request_refresh()

    async def refresh_bank_sync(call: ServiceCall) -> None:
        """Force an immediate poll — the integration's data path is
        its API sync, so a sync refresh IS a coordinator refresh."""
        coord = _coordinator(hass)
        await coord.async_request_refresh()
        if not coord.last_update_success:
            raise HomeAssistantError(
                "Family Finance refresh failed — check the app is "
                "reachable and credentials are valid")

    if hass.services.has_service(DOMAIN, SERVICE_ADD_TRANSACTION):
        return
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_TRANSACTION, add_transaction,
        schema=_SCHEMA_ADD_TRANSACTION)
    hass.services.async_register(
        DOMAIN, SERVICE_RECORD_OVERTIME, record_overtime,
        schema=_SCHEMA_RECORD_OVERTIME)
    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH_BANK_SYNC, refresh_bank_sync)
