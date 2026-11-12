"""Polling coordinator — entities go unavailable on failure, never
stale-wrong (issue #100)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import FamilyFinanceAuthError, FamilyFinanceClient, FamilyFinanceError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .energy import EnergyStatistics
from .events import process_failure, process_summary
from .outbox import EventDrain

_LOGGER = logging.getLogger(__name__)


class FamilyFinanceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the app's read endpoints on a fixed interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: FamilyFinanceClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self._failed = False
        self.entry = entry
        self.options_snapshot = dict(entry.options)
        self.energy = EnergyStatistics(hass, client)
        self.drain = EventDrain(
            client, entry.data.get("event_cursor"), self._persist_cursor
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            new = await self.client.async_fetch_summary()
            new["items"] = await self.client.async_fetch_items()
            await self.energy.poll()
            new["events"] = await self.drain.poll()
        except FamilyFinanceAuthError as exc:
            raise ConfigEntryAuthFailed(
                "Family Finance service token rejected"
            ) from exc
        except FamilyFinanceError as exc:
            process_failure(self.hass, self._failed, self.entry.entry_id)
            self._failed = True
            # UpdateFailed → last_update_success=False → unavailable
            raise UpdateFailed(f"Family Finance unreachable: {exc}") from exc
        # automation events compare consecutive payloads (issue #102)
        process_summary(self.hass, self.data, new, self.entry.entry_id)
        self._failed = False
        return new

    def _persist_cursor(self, cursor: int) -> None:
        if self.entry.data.get("event_cursor") != cursor:
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, "event_cursor": cursor}
            )
