"""Independent synthetic HMRC vectors; never use personal payroll fixtures.

PAYE v24 §§4.3,4.4,4.5,5.4; CWG2 exact-percentage NI;
2026/27 SL3 postgraduate worked example. See docs/PAYROLL.md.
"""

import pytest

from app.domain.tax import PensionMode, TaxError, compute_period, embedded_rule_sets


def params():
    return next(
        s["params"]
        for s in embedded_rule_sets()
        if s["tax_year"] == "2026-27" and s["jurisdiction"] == "england_wales"
    )


@pytest.mark.parametrize(
    "code,gross,expected",
    [
        ("1257L", 250000, 29020),
        ("C1257L", 250000, 29020),
        ("1257L M1", 250000, 29020),
        ("BR", 250099, 50000),
        ("D0", 250099, 100000),
        ("K500", 250000, 58340),
        ("1257L", 500000, 95206),
    ],
)
def test_paye_published_rounding(code, gross, expected):
    assert compute_period(gross_pence=gross, tax_code=code, params=params()).tax_pence == expected


def test_code_already_encodes_allowance_no_second_automatic_taper():
    assert (
        compute_period(gross_pence=1250000, tax_code="1257L", params=params()).tax_pence == 400320
    )


def test_regulatory_limit_applies_to_ordinary_codes():
    d = compute_period(
        gross_pence=100000,
        tax_code="1257L",
        params=params(),
        period=12,
        ytd_taxable_gross_pence=10000000,
    )
    assert d.tax_pence == 50000


@pytest.mark.parametrize(
    "gross,periods,expected",
    [
        (300000, 12, 15616),
        (250008, 12, 11617),
        (50000, 52, 2064),
        (418900, 12, 25128),
        (500000, 12, 26750),
    ],
)
def test_published_ni_period_thresholds_and_rounding(gross, periods, expected):
    assert (
        compute_period(
            gross_pence=gross, tax_code="NT", params=params(), periods_per_year=periods
        ).ni_pence
        == expected
    )


@pytest.mark.parametrize(
    "mode,pension,expected",
    [
        (PensionMode.none, 0, 4900),
        (PensionMode.net_pay, 20000, 4900),
        (PensionMode.salary_sacrifice, 20000, 3100),
    ],
)
def test_loans_use_ni_earnings_and_whole_pounds(mode, pension, expected):
    assert (
        compute_period(
            gross_pence=300000,
            tax_code="NT",
            params=params(),
            student_loan_plan="plan2",
            pension_mode=mode,
            pension_pence=pension,
        ).student_loan_pence
        == expected
    )


def test_sl3_published_postgraduate_example():
    assert (
        compute_period(
            gross_pence=208300, tax_code="NT", params=params(), student_loan_plan="postgrad"
        ).student_loan_pence
        == 1900
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(ni_category="?"),
        dict(student_loan_plan="?"),
        dict(pension_pence=300001),
        dict(gross_pence=-1),
    ],
)
def test_invalid_calculation_inputs_rejected(kwargs):
    with pytest.raises(TaxError):
        compute_period(**(dict(gross_pence=300000, tax_code="1257L", params=params()) | kwargs))


@pytest.mark.parametrize(
    "code,expected", [("BR", 90000), ("BR M1", 60000), ("NT", -30000), ("NT M1", 0)]
)
def test_flat_codes_keep_their_cumulative_or_non_cumulative_basis(code, expected):
    assert (
        compute_period(
            gross_pence=300000,
            tax_code=code,
            params=params(),
            period=2,
            ytd_taxable_gross_pence=300000,
            ytd_tax_pence=30000,
        ).tax_pence
        == expected
    )


@pytest.mark.parametrize("gross,expected", [(314200, 62840), (314201, 62846)])
def test_paye_selects_band_before_flooring_taxable_pay(gross, expected):
    assert compute_period(gross_pence=gross, tax_code="0T", params=params()).tax_pence == expected


def test_plan5_collection_not_before_april_2026():
    old = next(
        s["params"]
        for s in embedded_rule_sets()
        if s["tax_year"] == "2025-26" and s["jurisdiction"] == "england_wales"
    )
    assert (
        compute_period(
            gross_pence=300000, tax_code="NT", params=old, student_loan_plan="plan5"
        ).student_loan_pence
        == 0
    )
