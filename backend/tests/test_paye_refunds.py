"""Hand-calculated cumulative refunds: HMRC PAYE11090 and regulation 23(6)."""

import pytest

from app.domain import payroll_totals, tax
from app.persistence.models import (
    PayrollComponent,
    PayrollComponentKind,
    PayrollRun,
    PayrollRunStatus,
)


@pytest.mark.parametrize(
    "code,gross,ytd_gross,ytd_tax,expected_tax,expected_net",
    [
        # £2,000 YTD income is below month-2 £2,095 allowance: repay prior £200.
        ("1257L", 100000, 100000, 20000, -20000, 120000),
        ("1257L", 0, 100000, 20000, -20000, 20000),
        # K500: £7,833.33 taxable: £6,283.33@20% + £1,550@40% = £1,876.66.
        # Prior £3,000 paid => £1,123.34 refund, exempt from the half-pay limit.
        ("K500", 100000, 600000, 300000, -112334, 212334),
    ],
)
def test_cumulative_refund_goldens(code, gross, ytd_gross, ytd_tax, expected_tax, expected_net):
    params = next(
        row["params"]
        for row in tax.embedded_rule_sets()
        if row["tax_year"] == "2025-26" and row["jurisdiction"] == "england_wales"
    )
    result = tax.compute_period(
        gross_pence=gross,
        tax_code=code,
        params=params,
        period=2,
        ytd_taxable_gross_pence=ytd_gross,
        ytd_tax_pence=ytd_tax,
    )
    assert result.tax_pence == expected_tax
    assert result.net_pence == expected_net


@pytest.mark.parametrize(
    "code,frequency", [("1257LM1", 12), ("1257LW1", 52), ("K500M1", 12), ("K500W1", 52)]
)
def test_non_cumulative_codes_do_not_refund_previous_period_tax(code, frequency):
    params = next(
        row["params"]
        for row in tax.embedded_rule_sets()
        if row["tax_year"] == "2025-26" and row["jurisdiction"] == "england_wales"
    )
    plain = tax.compute_period(
        gross_pence=100000, tax_code=code, params=params, period=2, periods_per_year=frequency
    )
    previous = tax.compute_period(
        gross_pence=100000,
        tax_code=code,
        params=params,
        period=2,
        periods_per_year=frequency,
        ytd_taxable_gross_pence=600000,
        ytd_tax_pence=300000,
    )
    assert previous == plain
    assert previous.tax_pence >= 0


def test_native_reconciled_refund_variance_increases_net():
    run = PayrollRun(
        employment_id=1,
        gross_pence=100000,
        net_pence=212334,
        rule_set_version=2,
        status=PayrollRunStatus.reconciled,
    )
    run.components = [
        PayrollComponent(kind=PayrollComponentKind.tax, forecast_pence=112334, actual_pence=120000)
    ]
    assert payroll_totals.totals_for_run(run).actual_net_pence == 220000


def test_payrun_carries_a_refund_as_negative_tax_paid(session, household):
    from datetime import date
    from decimal import Decimal

    from app.domain import payroll, payrun
    from app.persistence.models import Employment, UserProfile

    owner = UserProfile(household_id=household.id, display_name="Synthetic", role="member")
    session.add(owner)
    session.flush()
    e = Employment(
        household_id=household.id,
        profile_id=owner.id,
        employer="Synthetic",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
    )
    session.add(e)
    session.flush()
    payroll.add_salary_revision(session, e, date(2025, 1, 1), 3600000)
    payroll.add_salary_revision(session, e, date(2025, 5, 1), 365000)
    payroll.add_salary_revision(session, e, date(2025, 6, 1), 3650000)
    payroll.add_tax_code_revision(session, e, date(2025, 1, 1), "1257L")
    april = payrun.generate(
        session, e, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    may = payrun.generate(
        session, e, start=date(2025, 5, 1), end=date(2025, 5, 31), pay_date=date(2025, 5, 28)
    )
    june = payrun.generate(
        session, e, start=date(2025, 6, 1), end=date(2025, 6, 30), pay_date=date(2025, 6, 28)
    )

    def tax_component(run):
        return next(c.forecast_pence for c in run.components if c.kind is PayrollComponentKind.tax)

    assert tax_component(april) == -38228
    assert tax_component(may) == 14750
    assert tax_component(june) == -39050
