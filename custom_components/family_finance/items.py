"""Bounded opt-in item selection shared by options flow and sensor platform."""

from __future__ import annotations

from typing import Any

ITEM_FAMILIES = ("bills", "accounts", "goals")
ENTITY_CAP = 100


def selected_items(
    items: list[dict[str, Any]], options: dict[str, Any]
) -> list[dict[str, Any]]:
    chosen = []
    for item in items:
        family = item["family"]
        mode = options.get(f"{family}_mode", "none")
        selected = options.get(f"{family}_selected", [])
        if mode == "all" or (mode == "selected" and str(item["id"]) in selected):
            chosen.append(item)
    cap = max(1, min(ENTITY_CAP, int(options.get("entity_cap", ENTITY_CAP))))
    return sorted(chosen, key=lambda item: (item["family"], str(item["id"])))[:cap]
