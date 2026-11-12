"""Configured recorder statistics to idempotent daily watt-hour readings."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import FamilyFinanceClient


class EnergyStatistics:
    def __init__(self, hass: HomeAssistant, client: FamilyFinanceClient) -> None:
        self.hass = hass
        self.client = client
        self._seen: dict[tuple[str, str], int] = {}
        self._staged: list[tuple[date, int]] = []

    async def poll(self) -> None:
        config = await self.client.async_energy_config()
        meter = config.get("meter_entity")
        if not meter or "recorder" not in self.hass.config.components:
            return
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import (
            statistics_during_period,
        )

        end = dt_util.start_of_local_day(dt_util.now())
        start = dt_util.start_of_local_day(end - timedelta(days=7))
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            dt_util.as_utc(start),
            dt_util.as_utc(end),
            {meter},
            "day",
            {"energy": "Wh"},
            {"change"},
        )
        self._staged = []
        for row in rows.get(meter, []):
            self._validate_and_stage(meter, row, start, end)
        # Never derive consumption from cumulative states. Recorder's `change`
        # accounts for total-increasing meter resets and requested unit conversion.
        for day, wh in self._staged:
            key = (meter, day.isoformat())
            if self._seen.get(key) != wh:
                await self.client.async_record_energy(day, wh)
                self._seen[key] = wh
        self._staged = []

    def _validate_and_stage(
        self, meter: str, row: dict[str, Any], start: datetime, end: datetime
    ) -> None:
        raw = row.get("change")
        if (
            isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or not math.isfinite(raw)
            or raw < 0
        ):
            return
        try:
            begin = row["start"]
            finish = row["end"]
            begin = dt_util.as_local(
                begin
                if isinstance(begin, datetime)
                else dt_util.utc_from_timestamp(begin)
            )
            finish = dt_util.as_local(
                finish
                if isinstance(finish, datetime)
                else dt_util.utc_from_timestamp(finish)
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        day_start = dt_util.start_of_local_day(begin)
        day_end = dt_util.start_of_local_day(begin + timedelta(days=1))
        if begin != day_start or finish != day_end or begin < start or finish > end:
            return
        self._staged.append((begin.date(), round(raw)))
