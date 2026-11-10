"""Extractor wiring role dispatch: sheet aliases resolve to generic
roles (explicit local-map declarations win over keyword inference) —
nothing household-specific is hardcoded."""

import pytest

from app.migration.wiring import role_of


@pytest.mark.parametrize(("alias", "role"), [
    ("Piecework Co", "piecework"),
    ("Acme Piecework Ledger", "piecework"),
    ("Xmas", "xmas"),
    ("Christmas 2026", "xmas"),
    ("FAMILY_LOAN_LEDGER", "loan"),
    ("Overtime", "overtime"),
    ("Salary History", "salary_history"),
    ("Tax Tables", "tax_tables"),
    ("Monthly Model", "monthly_model"),
    ("MEMBER2_BUDGET", "member2"),
])
def test_role_inferred_from_generic_keywords(
    alias: str, role: str,
) -> None:
    assert role_of(alias) == role


def test_explicit_role_overrides_keywords() -> None:
    """A local map can bind any sheet to any role — no naming
    convention is required for dispatch."""
    assert role_of("Grandad's Sheet", {"Grandad's Sheet": "xmas"}) \
        == "xmas"
    # declared role wins even when the alias also carries a keyword
    assert role_of("Overtime", {"Overtime": "piecework"}) == "piecework"


def test_unknown_alias_yields_no_extractor() -> None:
    assert role_of("Misc Notes") is None
