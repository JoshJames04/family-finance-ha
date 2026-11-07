"""Seed the dev environment with a synthetic household dataset.

Writes deterministic, fictional fixture data to the app data directory.
Never contains real financial values — see tests/fixtures/README.md.
Later schema work (Epic 3) will consume this fixture shape via real models.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Env var first (survives docker compose exec on Windows where a positional
# "/data" arg gets path-mangled by MSYS), then argv, then local default.
DATA_DIR = Path(
    os.environ.get("FF_DATA_DIR") or (sys.argv[1] if len(sys.argv) > 1 else "data")
)


def synthetic_household() -> dict:
    return {
        "household": {"name": "Test Household", "locale": "en-GB", "currency": "GBP"},
        "members": [
            {"name": "Bob Example", "role": "admin"},
            {"name": "Sam Example", "role": "member"},
        ],
        "accounts": [
            {"name": "Bob Personal", "type": "personal", "opening_balance_pence": 250000},
            {"name": "Sam Personal", "type": "personal", "opening_balance_pence": 180000},
            {"name": "Joint Bills", "type": "bills", "opening_balance_pence": 90000},
            {"name": "Joint Everyday", "type": "joint", "opening_balance_pence": 45000},
            {"name": "Holiday Fund", "type": "savings", "opening_balance_pence": 120000},
        ],
        "categories": [
            {"name": "Household Bills", "children": ["Council Tax", "Energy", "Water", "Internet"]},
            {"name": "Food", "children": ["Groceries", "Work Food", "Eating Out"]},
            {"name": "Transport", "children": ["Fuel", "Public Transport"]},
            {"name": "Savings", "children": ["Holiday Fund", "Emergency Fund", "Christmas"]},
        ],
        "sample_transactions": [
            {"account": "Joint Bills", "date": "2026-11-01", "payee": "Contoso Energy",
             "category": "Energy", "amount_pence": -14250},
            {"account": "Joint Everyday", "date": "2026-11-02", "payee": "FreshMart",
             "category": "Groceries", "amount_pence": -8640},
            {"account": "Bob Personal", "date": "2026-11-01", "payee": "ACME Manufacturing Ltd",
             "category": "Salary", "amount_pence": 285000},
        ],
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / "seed.json"
    out.write_text(json.dumps(synthetic_household(), indent=2))
    print(f"seeded: {out}")


if __name__ == "__main__":
    main()
