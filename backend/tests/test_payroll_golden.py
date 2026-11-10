"""Issue #84 — HMRC golden validation suite.

Asserts the tax engine's output equals the pinned golden dataset
(`tests/golden/payroll_golden.json`) for a spread of salaries, codes,
plans and pension modes across every supported tax year and
jurisdiction. The suite also *pins the parameters themselves* — a
silent edit to uk_tax_rules.json fails here even if the engine is
correct.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.tax import (
    PensionMode,
    compute_period,
    embedded_rule_sets,
    rule_set,
    seed_rule_sets,
)
from app.persistence.models import TaxJurisdiction, TaxRuleSet

GOLDEN = (Path(__file__).resolve().parent / "golden"
          / "payroll_golden.json")
DATA = (Path(__file__).resolve().parents[1] / "app" / "domain" / "data"
        / "uk_tax_rules.json")


def _params(year: str, jur: str) -> Any:
    for s in embedded_rule_sets():
        if s["tax_year"] == year and s["jurisdiction"] == jur:
            return s["params"]
    raise AssertionError(f"no rule set {year}/{jur}")


_CASES = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _CASES,
                         ids=[c["desc"] for c in _CASES])
def test_golden(case: dict[str, Any]) -> None:
    d = compute_period(
        gross_pence=case["gross_pence"], tax_code=case["code"],
        params=_params(case["year"], case["jurisdiction"]),
        period=case.get("period", 1),
        ytd_taxable_gross_pence=case.get("ytd_gross_pence", 0),
        ytd_tax_pence=case.get("ytd_tax_pence", 0),
        ni_category=case.get("ni_category", "A"),
        student_loan_plan=case.get("plan", "none"),
        pension_mode=PensionMode(case.get("pension_mode", "none")),
        pension_pence=case.get("pension_pence", 0))
    e = case["expect"]
    assert d.tax_pence == e["tax"], case["desc"]
    assert d.ni_pence == e["ni"], case["desc"]
    assert d.student_loan_pence == e["sl"], case["desc"]
    assert d.net_pence == e["net"], case["desc"]


# Pinned published parameters — the suite fails if the embedded data
# drifts. Sources: docs/TAX-RULES.md.
PINNED: dict[tuple[str, str], dict[str, Any]] = {
    ("2024-25", "england_wales"): {
        "pa": 1_257_000, "taper": 10_000_000,
        "bands": [3_770_000, 12_514_000, None],
        "rates": [2000, 4000, 4500],
        "pt": 1_257_000, "uel": 5_027_000,
        "catA": [800, 200],
        "plans": {"plan1": 2_499_000, "plan2": 2_729_500,
                  "plan4": 3_139_500, "plan5": 2_500_000,
                  "postgrad": 2_100_000},
    },
    ("2025-26", "england_wales"): {
        "pa": 1_257_000, "taper": 10_000_000,
        "bands": [3_770_000, 12_514_000, None],
        "rates": [2000, 4000, 4500],
        "pt": 1_257_000, "uel": 5_027_000,
        "catA": [800, 200],
        "plans": {"plan1": 2_606_500, "plan2": 2_847_000,
                  "plan4": 3_274_500, "plan5": 2_500_000,
                  "postgrad": 2_100_000},
    },
    ("2024-25", "scotland"): {
        "pa": 1_257_000, "taper": 10_000_000,
        "bands": [230_600, 1_399_100, 3_109_200,
                  6_243_000, 12_514_000, None],
        "rates": [1900, 2000, 2100, 4200, 4500, 4800],
        "pt": 1_257_000, "uel": 5_027_000,
        "catA": [800, 200],
        "plans": {"plan1": 2_499_000, "plan2": 2_729_500,
                  "plan4": 3_139_500, "plan5": 2_500_000,
                  "postgrad": 2_100_000},
    },
    ("2025-26", "scotland"): {
        "pa": 1_257_000, "taper": 10_000_000,
        "bands": [282_700, 1_492_100, 3_109_200,
                  6_243_000, 12_514_000, None],
        "rates": [1900, 2000, 2100, 4200, 4500, 4800],
        "pt": 1_257_000, "uel": 5_027_000,
        "catA": [800, 200],
        "plans": {"plan1": 2_606_500, "plan2": 2_847_000,
                  "plan4": 3_274_500, "plan5": 2_500_000,
                  "postgrad": 2_100_000},
    },
    ("2026-27", "england_wales"): {
        "pa": 1_257_000, "taper": 10_000_000,
        "bands": [3_770_000, 12_514_000, None],
        "rates": [2000, 4000, 4500],
        "pt": 1_257_000, "uel": 5_027_000,
        "catA": [800, 200],
        "plans": {"plan1": 2_690_000, "plan2": 2_938_500,
                  "plan4": 3_379_500, "plan5": 2_500_000,
                  "postgrad": 2_100_000},
    },
    ("2026-27", "scotland"): {
        "pa": 1_257_000, "taper": 10_000_000,
        "bands": [396_700, 1_695_600, 3_109_200,
                  6_243_000, 12_514_000, None],
        "rates": [1900, 2000, 2100, 4200, 4500, 4800],
        "pt": 1_257_000, "uel": 5_027_000,
        "catA": [800, 200],
        "plans": {"plan1": 2_690_000, "plan2": 2_938_500,
                  "plan4": 3_379_500, "plan5": 2_500_000,
                  "postgrad": 2_100_000},
    },
}


@pytest.mark.parametrize("key", sorted(PINNED.keys()))
def test_parameters_pinned(key: tuple[str, str]) -> None:
    """Every supported year×jurisdiction's published values are pinned."""
    year, jur = key
    p = _params(year, jur)
    want = PINNED[key]
    assert p["personal_allowance"] == want["pa"]
    assert p["taper_start"] == want["taper"]
    assert [b["limit"] for b in p["bands"]] == want["bands"]
    assert [b["rate"] for b in p["bands"]] == want["rates"]
    assert p["ni"]["primary_threshold"] == want["pt"]
    assert p["ni"]["upper_earnings_limit"] == want["uel"]
    assert p["ni"]["categories"]["A"] == want["catA"]
    for plan, threshold in want["plans"].items():
        assert p["student_loans"][plan][0] == threshold


def test_all_supported_sets_covered() -> None:
    """AC: suite pins EVERY supported year's parameters — no set may
    exist in the data file without a golden pin above."""
    have = {(s["tax_year"], s["jurisdiction"])
            for s in embedded_rule_sets()}
    assert have == set(PINNED.keys()), (
        f"rule sets without a pin: {have - set(PINNED.keys())}; "
        f"pins without a rule set: {set(PINNED.keys()) - have}")


def test_version_bump_selects_latest(session: Session) -> None:
    """Version bump workflow (#84 AC): a higher-version row supersedes;
    older rows stay readable for reproduction."""
    seed_rule_sets(session)  # v1 exists before the bump lands
    mutated = dict(_params("2025-26", "england_wales"))
    mutated["personal_allowance"] = 999_999
    session.add(TaxRuleSet(
        tax_year="2025-26", jurisdiction="scotland", version=2,
        params=json.dumps(mutated)))
    session.flush()
    latest = rule_set(session, "2025-26", TaxJurisdiction.scotland)
    assert latest.version == 2
    assert json.loads(latest.params)["personal_allowance"] == 999_999
    v1 = session.execute(
        select(TaxRuleSet).where(
            TaxRuleSet.tax_year == "2025-26",
            TaxRuleSet.jurisdiction == TaxJurisdiction.scotland,
            TaxRuleSet.version == 1)
    ).scalar_one()
    assert v1.version == 1  # historical version still pinned
