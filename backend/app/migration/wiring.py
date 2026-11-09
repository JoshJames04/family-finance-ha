"""Extractor wiring (issue #56): build the full per-sheet extractor set
from the structural inventory + the local real-name map.

Sheet identity comes from the gitignored ``migration-map.local.json``
(real name → pseudonym) — real names stay out of committed code. The
inventory's table refs drive every section decision; nothing here reads
cell values.
"""

from __future__ import annotations

import re
from typing import Any

from app.migration.extractors import (
    BudgetYearExtractor,
    LoanLedgerExtractor,
    OvertimeExtractor,
    PayrollExtractor,
    SalaryHistoryExtractor,
    SlowBoatExtractor,
    TaxTablesExtractor,
    XmasExtractor,
    cached_values,
)

_YEAR_RE = re.compile(r"(20\d{2})")


def build_extractors(
    *,
    spec: dict[str, Any],
    real_names: dict[str, str],  # alias → real sheet name
    household_id: int,
    owner_profile_id: int | None,
    member2_profile_id: int | None,
    employment_id: int,
    account_id: int,
    jurisdiction: str,
    loan_party: str,
    workbook_path: str,
) -> list[Any]:
    """One extractor per known sheet; unmapped sheets stay staged."""
    cache = cached_values(workbook_path)
    extractors: list[Any] = []
    for alias, sheet in spec["sheets"].items():
        real = real_names.get(alias, alias)
        tables = sheet.get("tables") or {}
        year = _YEAR_RE.search(real)
        a = alias.upper()
        if year and any(t.lower().startswith("tblincome")
                        for t in tables):
            extractors.append(BudgetYearExtractor(
                real, household_id=household_id,
                owner_profile_id=owner_profile_id,
                year=int(year.group(1)), name="Annual", tables=tables))
        elif "MEMBER2" in a:
            extractors.append(BudgetYearExtractor(
                real, household_id=household_id,
                owner_profile_id=member2_profile_id,
                year=int(year.group(1)) if year else 2025,
                name="Member2", tables=tables, all_owner=True))
        elif "LOAN" in a:
            extractors.append(LoanLedgerExtractor(
                real, household_id, loan_party))
        elif alias == "Overtime":
            extractors.append(OvertimeExtractor(
                real, employment_id, cache))
        elif alias == "Salary History":
            extractors.append(SalaryHistoryExtractor(
                real, employment_id))
        elif alias == "Tax Tables":
            extractors.append(TaxTablesExtractor(
                real, jurisdiction, tables))
        elif alias == "Monthly Model":
            extractors.append(PayrollExtractor(
                real, employment_id, jurisdiction, cache))
        elif alias == "Xmas":
            extractors.append(XmasExtractor(
                real, household_id, tables))
        elif alias == "Slow Boat":
            extractors.append(SlowBoatExtractor(
                real, household_id, account_id, cache))
    return extractors
