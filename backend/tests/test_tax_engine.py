"""Issue #81 — versioned UK tax engine.

Golden values are hand-computed from the GOV.UK-published parameters
embedded in uk_tax_rules.json (sources noted per case); all money in
integer pence, rates floored to the penny per HMRC convention.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.domain.tax import (
    PensionMode,
    TaxError,
    compute_period,
    embedded_rule_sets,
    parse_tax_code,
    rule_set,
    seed_rule_sets,
)
from app.persistence.models import TaxJurisdiction, TaxRuleSet

DATA = (Path(__file__).resolve().parents[1] / "app" / "domain" / "data"
        / "uk_tax_rules.json")


def _params(year: str, jur: str) -> Any:
    raw: Any = json.loads(DATA.read_text(encoding="utf-8"))
    for s in raw["sets"]:
        if s["tax_year"] == year and s["jurisdiction"] == jur:
            return s["params"]  # type: ignore[no-any-return]
    raise AssertionError(f"missing rule set {year}/{jur}")


EW = _params("2025-26", "england_wales")
SC = _params("2025-26", "scotland")
EW24 = _params("2024-25", "england_wales")


class TestGolden2025_26:
    """Hand-computed against GOV.UK 2025-26 rates:
    PA £12,570; bands 20/40/45%; NI 8% £12,570–£50,270 then 2%."""

    def test_30k_month1(self) -> None:
        # £2,500 gross; cum PA £1,047.50; taxable £1,452.50 @20% = £290.50
        d = compute_period(gross_pence=250_000, tax_code="1257L",
                           params=EW, period=1)
        assert d.tax_pence == 29_050
        # NI: (£2,500 − £1,047.50) @8% = £116.20
        assert d.ni_pence == 11_620
        assert d.net_pence == 250_000 - 29_050 - 11_620

    def test_60k_month1(self) -> None:
        # £5,000 gross; taxable £3,952.50; £3,141.67@20% + £810.83@40%
        d = compute_period(gross_pence=500_000, tax_code="1257L",
                           params=EW, period=1)
        assert d.tax_pence == 95_266
        # NI: £3,141.66@8% + £810.84@2%
        assert d.ni_pence == 26_754

    def test_150k_month1_tapered(self) -> None:
        # £12,500 gross; income >£125,140 ⇒ PA fully tapered to £0
        d = compute_period(gross_pence=1_250_000, tax_code="1257L",
                           params=EW, period=1)
        assert d.tax_pence == 447_524
        assert d.ni_pence == 41_754

    def test_cumulative_month3(self) -> None:
        """Cumulative semantics: cum tax on £7,500 − prior paid = same
        monthly £290.50 for a flat salary."""
        m1 = compute_period(gross_pence=250_000, tax_code="1257L",
                            params=EW, period=1)
        m3 = compute_period(gross_pence=250_000, tax_code="1257L",
                            params=EW, period=3,
                            ytd_taxable_gross_pence=500_000,
                            ytd_tax_pence=m1.tax_pence * 2)
        # cum: £7,500 − £3,142.50 = £4,357.50 @20% = £871.50 − £581 paid
        assert m3.tax_pence == 29_050

    def test_w1m1_ignores_ytd(self) -> None:
        d = compute_period(gross_pence=250_000, tax_code="1257L W1",
                           params=EW, period=9,
                           ytd_taxable_gross_pence=2_000_000,
                           ytd_tax_pence=500_000)
        assert d.tax_pence == 29_050  # period-only, no catch-up


class TestCodeVariants:
    def test_br_flats_everything(self) -> None:
        d = compute_period(gross_pence=250_000, tax_code="BR",
                           params=EW, period=1)
        assert d.tax_pence == 50_000

    def test_d0_d1(self) -> None:
        assert compute_period(gross_pence=250_000, tax_code="D0",
                              params=EW).tax_pence == 100_000
        assert compute_period(gross_pence=250_000, tax_code="D1",
                              params=EW).tax_pence == 112_500

    def test_0t_no_allowance(self) -> None:
        d = compute_period(gross_pence=250_000, tax_code="0T",
                           params=EW, period=1)
        assert d.tax_pence == 50_000

    def test_k_code_adds_income(self) -> None:
        # K500: allowance −£5,000 → taxable = £2,500 + £416.66 = £2,916.66
        d = compute_period(gross_pence=250_000, tax_code="K500",
                           params=EW, period=1)
        assert d.tax_pence == 58_333

    def test_nt_no_tax(self) -> None:
        assert compute_period(gross_pence=250_000, tax_code="NT",
                              params=EW).tax_pence == 0

    def test_scottish_prefix(self) -> None:
        # S1257L on Scottish bands: starter/basic/intermediate split
        d = compute_period(gross_pence=250_000, tax_code="S1257L",
                           params=SC, period=1)
        # £1,452.50 taxable: £235.58@19% + £1,007.83@20% + £209.09@21%
        assert d.tax_pence == 29_022


class TestNiAndLoans:
    def test_ni_category_m_zero_above_uel(self) -> None:
        d = compute_period(gross_pence=500_000, tax_code="1257L",
                           params=EW, ni_category="M")
        # under-21: 8% to UEL, 0% above → only the £251.33 band
        assert d.ni_pence == 25_133

    def test_ni_category_c_zero(self) -> None:
        d = compute_period(gross_pence=500_000, tax_code="1257L",
                           params=EW, ni_category="C")
        assert d.ni_pence == 0

    def test_student_loan_plans(self) -> None:
        # Plan 2 2025-26: threshold £28,470 → £2,372.50/mo
        p2 = compute_period(gross_pence=250_000, tax_code="1257L",
                            params=EW, student_loan_plan="plan2")
        # excess £127.50 @9% = £11.475 → floor £11.47
        assert p2.student_loan_pence == 1_147
        p5 = compute_period(gross_pence=250_000, tax_code="1257L",
                            params=EW, student_loan_plan="plan5")
        # threshold £25,000 → £2,083.33; excess £416.67 @9% = £37.50
        assert p5.student_loan_pence == 3_750
        none = compute_period(gross_pence=250_000, tax_code="1257L",
                              params=EW, student_loan_plan="none")
        assert none.student_loan_pence == 0


class TestPensionModes:
    def test_salary_sacrifice_reduces_both(self) -> None:
        d = compute_period(gross_pence=250_000, tax_code="1257L",
                           params=EW, pension_pence=12_500,
                           pension_mode=PensionMode.salary_sacrifice)
        assert d.taxable_gross_pence == 237_500
        assert d.tax_pence == 26_550   # (£2,375−£1,047.50)@20%
        assert d.ni_pence == 10_620    # also reduced

    def test_net_pay_reduces_tax_only(self) -> None:
        d = compute_period(gross_pence=250_000, tax_code="1257L",
                           params=EW, pension_pence=12_500,
                           pension_mode=PensionMode.net_pay)
        assert d.tax_pence == 26_550
        assert d.ni_pence == 11_620    # NI still on full gross

    def test_ras_no_deduction_effect(self) -> None:
        d = compute_period(gross_pence=250_000, tax_code="1257L",
                           params=EW, pension_pence=12_500,
                           pension_mode=PensionMode.relief_at_source)
        assert d.tax_pence == 29_050 and d.ni_pence == 11_620
        assert d.net_pence == 250_000 - 29_050 - 11_620 - 12_500


class TestVersioning:
    def test_year_switch_changes_result(self, session: Session) -> None:
        """AC: switching tax year changes results without code change —
        Plan 1 threshold rose £24,990→£26,065 between years."""
        for params, expect in (
                (EW24, (250_000 - 2_499_000 // 12) * 9 // 100),
                (EW, (250_000 - 2_606_500 // 12) * 9 // 100)):
            d = compute_period(gross_pence=250_000, tax_code="1257L",
                               params=params,
                               student_loan_plan="plan1")
            assert d.student_loan_pence == expect

    def test_seed_idempotent(self, session: Session) -> None:
        assert seed_rule_sets(session) == len(embedded_rule_sets())
        assert seed_rule_sets(session) == 0
        row = rule_set(session, "2025-26", TaxJurisdiction.england_wales)
        assert json.loads(row.params)["personal_allowance"] == 1_257_000

    def test_rule_set_lookup_latest_version(
            self, session: Session) -> None:
        seed_rule_sets(session)
        session.add(TaxRuleSet(
            tax_year="2025-26", jurisdiction="scotland", version=2,
            params="{}"))
        session.flush()
        assert rule_set(session, "2025-26",
                        TaxJurisdiction.scotland).version == 2


class TestCodeParsing:
    def test_variants(self) -> None:
        assert parse_tax_code("1257L").allowance_pence == 1_257_000
        assert parse_tax_code("S1257L").prefix == "S"
        assert parse_tax_code("K500").allowance_pence == -500_000
        assert not parse_tax_code("1257L M1").cumulative
        with pytest.raises(TaxError):
            parse_tax_code("GIBBERISH")


class TestMetaAndDefectD:
    def test_meta_rate_change_breaks_goldens(self) -> None:
        """Meta-test (#81 AC): mutating a statutory rate in the rule
        set MUST change the golden result — proof the data actually
        drives the math, not hidden constants."""
        mutated = json.loads(json.dumps(EW))
        mutated["bands"][0]["rate"] = 2100  # 20% → 21%
        d = compute_period(gross_pence=250_000, tax_code="1257L",
                           params=mutated, period=1)
        assert d.tax_pence != 29_050
        assert d.tax_pence == 30_502  # £1,452.50 @21%

    def test_defect_d_regression(self) -> None:
        """Workbook defect D: the Income sheet approximated take-home
        with a hardcoded =B*0.76 (flat 24% deduction). The engine must
        reproduce the true HMRC deductions instead — for £2,500/month
        2025-26 that is £290.50 tax + £116.20 NI = £406.70, i.e. net
        £2,093.30 — not the £2,500×0.76 = £1,900 the workbook gave."""
        workbook_net = int(250_000 * 0.76)
        d = compute_period(gross_pence=250_000, tax_code="1257L",
                           params=EW, period=1)
        assert workbook_net == 190_000
        assert d.net_pence == 209_330
        assert d.net_pence != workbook_net
