"""Extractor wiring (issue #56): build the full per-sheet extractor set
from the structural inventory + the local real-name map.

Sheet identity comes from the gitignored ``migration-map.local.json``
(real name → pseudonym) — real names stay out of committed code. The
inventory's table refs drive every section decision; nothing here reads
cell values.

Dispatch is role-based, not name-based: each sheet alias resolves to a
generic role (``piecework``, ``xmas``, ``loan`` …). Roles can be declared
explicitly in the local map's optional ``roles`` section, and are
otherwise inferred from generic keywords in the alias. Other households
map their own sheets to these roles — nothing household-specific is
hardcoded.
"""

from __future__ import annotations

import re
from typing import Any

from app.migration.extractors import (
    BudgetYearExtractor,
    LoanLedgerExtractor,
    OvertimeExtractor,
    PayrollExtractor,
    PieceworkExtractor,
    SalaryHistoryExtractor,
    TaxTablesExtractor,
    XmasExtractor,
    cached_values,
)

_YEAR_RE = re.compile(r"(20\d{2})")

# Generic alias keyword → extractor role. Order matters: the first
# keyword found in the (uppercased) alias wins.
_ROLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("MEMBER2", "member2"),
    ("LOAN", "loan"),
    ("OVERTIME", "overtime"),
    ("SALARY", "salary_history"),
    ("TAX", "tax_tables"),
    ("MONTHLY", "monthly_model"),
    ("XMAS", "xmas"),
    ("CHRISTMAS", "xmas"),
    ("PIECEWORK", "piecework"),
)


def role_of(alias: str, roles: dict[str, str] | None = None) -> str | None:
    """Resolve a sheet alias to an extractor role.

    Explicit ``roles`` declarations in the local map win; otherwise the
    role is inferred from generic keywords in the alias.
    """
    if roles and alias in roles:
        return roles[alias]
    a = alias.upper()
    for kw, role in _ROLE_KEYWORDS:
        if kw in a:
            return role
    return None


def build_extractors(
    *,
    spec: dict[str, Any],
    real_names: dict[str, str],  # alias → real sheet name
    roles: dict[str, str] | None = None,  # alias → extractor role
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
        role = role_of(alias, roles)
        if year and any(t.lower().startswith("tblincome")
                        for t in tables):
            extractors.append(BudgetYearExtractor(
                real, household_id=household_id,
                owner_profile_id=owner_profile_id,
                year=int(year.group(1)), name="Annual", tables=tables,
                cache=cache))
        elif role == "member2":
            extractors.append(BudgetYearExtractor(
                real, household_id=household_id,
                owner_profile_id=member2_profile_id,
                year=int(year.group(1)) if year else 2025,
                name="Member2", tables=tables, all_owner=True,
                cache=cache))
        elif role == "loan":
            extractors.append(LoanLedgerExtractor(
                real, household_id, loan_party))
        elif role == "overtime":
            extractors.append(OvertimeExtractor(
                real, employment_id, cache))
        elif role == "salary_history":
            extractors.append(SalaryHistoryExtractor(
                real, employment_id))
        elif role == "tax_tables":
            extractors.append(TaxTablesExtractor(
                real, jurisdiction, tables))
        elif role == "monthly_model":
            extractors.append(PayrollExtractor(
                real, employment_id, jurisdiction, cache))
        elif role == "xmas":
            extractors.append(XmasExtractor(
                real, household_id, tables))
        elif role == "piecework":
            extractors.append(PieceworkExtractor(
                real, household_id, account_id, cache))
    return extractors
