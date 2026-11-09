"""Polling coordinator — entities go unavailable on failure, never
stale-wrong (issue #100)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import FamilyFinanceClient, FamilyFinanceError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .events import process_failure, process_summary

_LOGGER = logging.getLogger(__name__)


class FamilyFinanceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the app's read endpoints on a fixed interval."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry,
        client: FamilyFinanceClient,
    ) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, config_entry=entry,
            update_interval=DEFAULT_SCAN_INTERVAL)
        self.client = client
        self._failed = False

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            new = await self.client.async_fetch_summary()
        except FamilyFinanceError as exc:
            process_failure(self.hass, self._failed)
            self._failed = True
            # UpdateFailed → last_update_success=False → unavailable
            raise UpdateFailed(
                f"Family Finance unreachable: {exc}") from exc
        # automation events compare consecutive payloads (issue #102)
        process_summary(self.hass, self.data, new)
        self._failed = False
        return new
