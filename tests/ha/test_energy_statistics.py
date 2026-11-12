"""Recorder daily changes are the only energy-ingestion source."""

from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.util import dt as dt_util

from custom_components.family_finance.energy import EnergyStatistics


async def test_daily_wh_changes_idempotent_missing_and_reset_safe(hass):
    hass.config.components.add("recorder")
    end = dt_util.start_of_local_day(dt_util.now())
    begin = dt_util.start_of_local_day(end - timedelta(days=1))
    valid = {
        "start": begin.timestamp(),
        "end": end.timestamp(),
        "change": 1250.4,
        "sum": 900000,
    }  # cumulative stock is deliberately irrelevant
    rows = {
        "sensor.energy": [
            valid,
            {**valid, "change": None},
            {**valid, "change": -12},
            {**valid, "change": float("nan")},
            {
                **valid,
                "start": end.timestamp(),
                "end": (end + timedelta(hours=1)).timestamp(),
            },
            {**valid, "end": (end - timedelta(hours=1)).timestamp()},
        ]
    }
    client = Mock()
    client.async_energy_config = AsyncMock(
        return_value={"meter_entity": "sensor.energy"}
    )
    client.async_record_energy = AsyncMock()
    recorder = Mock()
    recorder.async_add_executor_job = AsyncMock(return_value=rows)
    ingestion = EnergyStatistics(hass, client)
    with patch("homeassistant.components.recorder.get_instance", return_value=recorder):
        await ingestion.poll()
        await ingestion.poll()
    client.async_record_energy.assert_awaited_once_with(begin.date(), 1250)
    args = recorder.async_add_executor_job.call_args.args
    assert args[-3:] == ("day", {"energy": "Wh"}, {"change"})


async def test_unconfigured_or_missing_statistics_never_fabricate_zero(hass):
    client = Mock()
    client.async_energy_config = AsyncMock(return_value={"meter_entity": None})
    client.async_record_energy = AsyncMock()
    ingestion = EnergyStatistics(hass, client)
    await ingestion.poll()
    client.async_record_energy.assert_not_called()


def test_completed_dst_day_accepts_23_hours_without_inventing_samples(hass):
    from datetime import datetime
    from unittest.mock import patch
    from zoneinfo import ZoneInfo

    timezone = ZoneInfo("Europe/London")
    begin = datetime(2026, 3, 29, tzinfo=timezone)
    end = datetime(2026, 3, 30, tzinfo=timezone)
    ingestion = EnergyStatistics(hass, Mock())
    with (
        patch(
            "custom_components.family_finance.energy.dt_util.as_local",
            side_effect=lambda value: value.astimezone(timezone),
        ),
        patch(
            "custom_components.family_finance.energy.dt_util.start_of_local_day",
            side_effect=lambda value: value.replace(
                hour=0, minute=0, second=0, microsecond=0
            ),
        ),
    ):
        ingestion._validate_and_stage(
            "sensor.energy",
            {
                "start": begin.timestamp(),
                "end": end.timestamp(),
                "change": 23000,
            },
            begin,
            end,
        )
    assert end.timestamp() - begin.timestamp() == 23 * 3600
    assert ingestion._staged == [(begin.date(), 23000)]
