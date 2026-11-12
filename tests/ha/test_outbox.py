"""Durable consumer and item opt-in acceptance cases."""

from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.family_finance.api import FamilyFinanceError
from custom_components.family_finance.items import selected_items
from custom_components.family_finance.outbox import EventDrain, family_for


async def test_first_install_skips_history_then_persists_catchup_cursor():
    client = Mock()
    client.async_fetch_events = AsyncMock(
        side_effect=[
            {"events": [{"seq": 4}], "next_cursor": 4},
            {
                "events": [{"seq": 5, "event_type": "movement.settled"}],
                "next_cursor": 5,
            },
        ]
    )
    save = Mock()
    drain = EventDrain(client, None, save)
    assert await drain.poll() == []
    client.async_fetch_events.assert_awaited_once_with(latest=True)
    assert (await drain.poll())[0]["seq"] == 5
    assert drain.cursor == 4
    drain.acknowledge()
    assert drain.cursor == 5
    save.assert_called_with(5)
    # Reload resumes the persisted position, never from zero.
    resumed = EventDrain(client, 5, save)
    client.async_fetch_events = AsyncMock(return_value={"events": [], "next_cursor": 5})
    assert await resumed.poll() == []
    client.async_fetch_events.assert_awaited_once_with(5)


async def test_crash_before_publication_replays_unacknowledged_batch():
    row = {"seq": 5, "event_type": "movement.settled"}
    client = Mock()
    client.async_fetch_events = AsyncMock(return_value={"events": [row], "next_cursor": 5})
    save = Mock()
    drain = EventDrain(client, 4, save)
    assert await drain.poll() == [row]
    assert drain.cursor == 4
    save.assert_not_called()
    # A crash loses the pending in-memory page but retains the old durable cursor.
    resumed = EventDrain(client, 4, save)
    assert await resumed.poll() == [row]
    resumed.acknowledge()
    assert resumed.cursor == 5
    save.assert_called_once_with(5)


async def test_partial_drain_failure_preserves_cursor_for_retry():
    client = Mock()
    client.async_fetch_events = AsyncMock(
        side_effect=[
            {"events": [{"seq": i} for i in range(1, 101)], "next_cursor": 100},
            FamilyFinanceError("offline"),
        ]
    )
    save = Mock()
    drain = EventDrain(client, 0, save)
    with pytest.raises(FamilyFinanceError):
        await drain.poll()
    assert drain.cursor == 0
    save.assert_not_called()


def test_item_selection_is_opt_in_rename_stable_and_capped():
    items = [
        {
            "family": "accounts",
            "id": str(i),
            "name": f"Name {i}",
            "value": i,
            "unit": "GBP",
        }
        for i in range(105)
    ]
    assert selected_items(items, {}) == []
    assert len(selected_items(items, {"accounts_mode": "all"})) == 100
    picked = selected_items(
        items, {"accounts_mode": "selected", "accounts_selected": ["3"]}
    )
    assert picked[0]["id"] == "3"
    items[3]["name"] = "Renamed"
    assert (
        selected_items(
            items, {"accounts_mode": "selected", "accounts_selected": ["3"]}
        )[0]["id"]
        == "3"
    )
    assert family_for("payroll.generated") == "payroll"
    assert family_for("gift.paid") == "gifts"
