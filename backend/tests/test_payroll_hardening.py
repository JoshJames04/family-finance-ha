"""Synthetic integration regressions for payroll trust boundaries."""

import calendar
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.api.schemas import PayrollRunOut
from app.domain import payroll, payroll_totals, payrun, tax
from app.domain.payroll import PayrollError
from app.persistence.models import (
    Employment,
    PayrollComponent,
    PayrollRun,
    PayrollRunStatus,
    UserProfile,
)
from app.persistence.models import (
    PayrollComponentKind as K,
)


@pytest.fixture
def emp(session, household):
    profile = UserProfile(household_id=household.id, display_name="Synthetic")
    session.add(profile)
    session.flush()
    e = Employment(
        household_id=household.id,
        profile_id=profile.id,
        employer="Synthetic employer",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
    )
    session.add(e)
    session.flush()
    payroll.add_salary_revision(session, e, date(2024, 1, 1), 3600000)
    payroll.add_tax_code_revision(session, e, date(2024, 1, 1), "1257L")
    return e


def prior(session, emp, **kwargs):
    row = PayrollRun(
        employment_id=emp.id,
        period_start=date(2025, 4, 1),
        period_end=date(2025, 4, 30),
        pay_date=date(2025, 4, 28),
        status=PayrollRunStatus.reconciled,
        tax_year="2025/26",
        rule_set_version=2,
        gross_pence=300000,
        net_pence=260000,
        components=[
            PayrollComponent(
                kind=K.base_salary, label="Base", forecast_pence=300000, actual_pence=300000
            ),
            PayrollComponent(kind=K.tax, label="Tax", forecast_pence=-40000, actual_pence=-40000),
        ],
    )
    for k, v in kwargs.items():
        setattr(row, k, v)
    if row.status is PayrollRunStatus.forecast:
        for c in row.components:
            c.actual_pence = None
    session.add(row)
    session.flush()
    return row


@pytest.mark.parametrize("month", [2, 4, 5])
def test_equal_monthly_policy(session, emp, month):
    emp.salary_policy = "equal_monthly"
    assert (
        payroll.gross_for_period(
            session,
            emp.id,
            date(2025, month, 1),
            date(2025, month, calendar.monthrange(2025, month)[1]),
        )
        == 300000
    )


def test_equal_monthly_cutoff_does_not_prorate_base(session, emp):
    emp.salary_policy = "equal_monthly"
    emp.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    run = payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    assert run.gross_pence == 300000
    assert run.period_start == date(2025, 3, 16)


def test_api_preserves_refund_sign(session, emp):
    run = prior(session, emp)
    run.components[1].actual_pence = 10000
    assert PayrollRunOut.model_validate(run).actual_net_pence == 310000
    assert payroll_totals.totals_for_run(run).actual_net_pence == 310000


def test_corrected_generation_returns_latest(session, emp):
    old = prior(session, emp)
    replacement = payrun.correct(session, old)
    assert (
        payrun.generate(
            session, emp, start=old.period_start, end=old.period_end, pay_date=old.pay_date
        ).id
        == replacement.id
    )


def test_reconcile_requires_independent_totals(session, emp):
    run = prior(session, emp, status=PayrollRunStatus.forecast)
    with pytest.raises(PayrollError, match="payslip gross and net"):
        payrun.reconcile(session, run, ({kind: 0 for kind in K} | {}))
    assert run.status is PayrollRunStatus.forecast


def test_verified_basis_and_slash_label_feed_ytd(session, emp):
    row = prior(session, emp)
    row.verified_taxable_pence = 275000
    row.verified_ytd_taxable_pence = 275000
    row.verified_ytd_tax_pence = 40000
    with patch.object(tax, "compute_period", wraps=tax.compute_period) as spy:
        payrun.generate(
            session, emp, start=date(2025, 5, 1), end=date(2025, 5, 31), pay_date=date(2025, 5, 28)
        )
    assert spy.call_args.kwargs["ytd_taxable_gross_pence"] == 275000
    assert spy.call_args.kwargs["ytd_tax_pence"] == 40000


def test_ambiguous_actual_history_requires_verified_basis(session, emp):
    prior(session, emp)
    with pytest.raises(PayrollError, match="verified taxable"):
        payrun.generate(
            session, emp, start=date(2025, 5, 1), end=date(2025, 5, 31), pay_date=date(2025, 5, 28)
        )


def test_tax_code_effective_at_payday(session, emp):
    r = prior(session, emp)
    r.verified_ytd_taxable_pence = 300000
    r.verified_ytd_tax_pence = 40000
    payroll.add_tax_code_revision(session, emp, date(2025, 5, 30), "BR")
    with patch.object(tax, "compute_period", wraps=tax.compute_period) as spy:
        payrun.generate(
            session, emp, start=date(2025, 5, 1), end=date(2025, 5, 31), pay_date=date(2025, 5, 28)
        )
    assert spy.call_args.kwargs["tax_code"] == "1257L"


def test_regular_weekend_payday_has_distinct_tax_date(session, emp):
    emp.payday_rule = '{"kind":"fixed_day","day":6,"adjust":"previous"}'
    run = payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 4)
    )
    assert run.pay_date == date(2025, 4, 4)
    assert run.statutory_pay_date == date(2025, 4, 6)
    assert run.tax_year == "2025-26"


def test_reconcile_aggregates_duplicate_kinds_once_and_marks_future_stale(session, emp):
    run = prior(session, emp, status=PayrollRunStatus.forecast)
    run.components.extend(
        [
            PayrollComponent(kind=K.overtime, label="First", forecast_pence=10000),
            PayrollComponent(kind=K.overtime, label="Second", forecast_pence=20000),
        ]
    )
    session.flush()
    future = prior(
        session,
        emp,
        period_start=date(2025, 5, 1),
        period_end=date(2025, 5, 31),
        pay_date=date(2025, 5, 28),
        status=PayrollRunStatus.forecast,
    )
    payrun.reconcile(
        session,
        run,
        ({kind: 0 for kind in K} | {K.base_salary: 300000, K.tax: -40000, K.overtime: 25000}),
        payslip_gross_pence=325000,
        payslip_net_pence=285000,
        taxable_pence=325000,
    )
    assert sum(c.actual_pence for c in run.components if c.kind is K.overtime) == 25000
    assert payroll_totals.totals_for_run(run).actual_net_pence == 285000
    assert future.forecast_stale


def test_bad_confirmation_does_not_partially_mutate(session, emp):
    run = prior(session, emp, status=PayrollRunStatus.forecast)
    with pytest.raises(PayrollError, match="balance"):
        payrun.reconcile(
            session,
            run,
            ({kind: 0 for kind in K} | {K.base_salary: 300000, K.tax: -40000}),
            payslip_gross_pence=300000,
            payslip_net_pence=250000,
            taxable_pence=300000,
        )
    assert run.status is PayrollRunStatus.forecast
    assert run.components[0].actual_pence is None


def test_forecast_rebuild_preview_is_nonmutating_and_actuals_preserved(session, emp):
    actual = prior(session, emp)
    actual.verified_ytd_taxable_pence = 300000
    actual.verified_ytd_tax_pence = 40000
    future = prior(
        session,
        emp,
        period_start=date(2025, 5, 1),
        period_end=date(2025, 5, 31),
        pay_date=date(2025, 5, 28),
        status=PayrollRunStatus.forecast,
        forecast_stale=True,
    )
    preview = payrun.rebuild_forecasts(session, emp, apply=False)
    assert future.net_pence == 260000 and future.forecast_stale
    assert preview["changes"][0]["run_id"] == future.id
    payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
    assert not future.forecast_stale
    assert future.net_pence != 260000
    assert actual.net_pence == 260000 and actual.status is PayrollRunStatus.reconciled


def test_rebuild_refuses_stale_preview_and_manual_override(session, emp):
    future = prior(session, emp, status=PayrollRunStatus.forecast)
    preview = payrun.rebuild_forecasts(session, emp, apply=False)
    payrun.apply_override(
        session,
        future,
        kind=K.allowance,
        delta_pence=100,
        reason="Explicit adjustment",
        actor="Synthetic",
    )
    with pytest.raises(PayrollError, match="changed"):
        payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
    preview = payrun.rebuild_forecasts(session, emp, apply=False)
    assert preview["warnings"]
    assert not preview["changes"]


def test_overtime_approval_and_edit_invalidate_forecast_without_rewriting_actuals(session, emp):
    from app.domain import overtime

    run = payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    original_net = run.net_pence
    entry = overtime.create_entry(session, emp, work_date=date(2025, 4, 10), hours=Decimal("4"))
    overtime.submit(entry)
    overtime.approve(entry)
    assert run.forecast_stale and run.net_pence == original_net
    original_rate = entry.hourly_rate_pence
    payroll.add_salary_revision(session, emp, date(2025, 4, 15), 4800000)
    assert entry.hourly_rate_pence == original_rate
    run.forecast_stale = False
    overtime.update_entry(session, entry, emp, hours=Decimal("5"))
    assert run.forecast_stale


def test_equal_monthly_partial_month_and_revision(session, emp):
    emp.salary_policy = "equal_monthly"
    payroll.add_salary_revision(session, emp, date(2025, 1, 16), 4800000)
    assert payroll.gross_for_period(session, emp.id, date(2025, 1, 1), date(2025, 1, 31)) == 351613
    assert payroll.gross_for_period(session, emp.id, date(2025, 2, 6), date(2025, 2, 28)) == 328571


def test_unsupported_history_never_uses_a_future_years_rules(session, emp):
    payroll.add_salary_revision(session, emp, date(2023, 1, 1), 3600000)
    payroll.add_tax_code_revision(session, emp, date(2023, 1, 1), "1257L")
    with pytest.raises(tax.TaxError, match="no rule set"):
        payrun.generate(
            session,
            emp,
            start=date(2023, 11, 1),
            end=date(2023, 11, 30),
            pay_date=date(2023, 11, 28),
            allow_stale_rules=True,
        )


def test_salary_revision_invalidates_forecast_and_preserves_actual_history(session, emp):
    actual = prior(session, emp)
    actual.verified_ytd_taxable_pence = 300000
    actual.verified_ytd_tax_pence = 40000
    future = payrun.generate(
        session, emp, start=date(2025, 5, 1), end=date(2025, 5, 31), pay_date=date(2025, 5, 28)
    )
    old_net = future.net_pence
    payroll.add_salary_revision(session, emp, date(2025, 5, 1), 4800000)
    assert future.forecast_stale and future.net_pence == old_net
    assert actual.status is PayrollRunStatus.reconciled and actual.net_pence == 260000


@pytest.mark.parametrize("policy", ["annual_daily", "equal_monthly"])
def test_regular_pay_estimate_and_first_run_share_salary_policy(session, emp, policy):
    from app.domain import schedule

    emp.salary_policy = policy
    emp.payday_rule = '{"kind":"fixed_day","day":28}'
    run = payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    assert schedule._employment_net_estimate(session, emp, run.pay_date) == run.net_pence


def test_pension_refund_can_be_in_printed_gross_without_inflating_taxable_pay(session, emp):
    run = prior(session, emp, status=PayrollRunStatus.forecast)
    payrun.reconcile(
        session,
        run,
        ({kind: 0 for kind in K} | {K.base_salary: 300000, K.tax: -40000, K.pension: 10000}),
        payslip_gross_pence=310000,
        payslip_net_pence=270000,
        taxable_pence=300000,
    )
    assert payroll_totals.totals_for_run(run).actual_gross_pence == 310000
    assert run.verified_taxable_pence == 300000


def test_signed_confirmation_refund_is_not_inferred_from_rate_version(session, emp):
    run = prior(session, emp, status=PayrollRunStatus.forecast, rule_set_version=0)
    payrun.reconcile(
        session,
        run,
        ({kind: 0 for kind in K} | {K.base_salary: 300000, K.tax: 10000}),
        payslip_gross_pence=300000,
        payslip_net_pence=310000,
        taxable_pence=300000,
    )
    assert run.signed_components is True
    assert PayrollRunOut.model_validate(run).actual_net_pence == 310000


def test_equal_monthly_joiner_after_overtime_cutoff_still_receives_partial_salary(session, emp):
    emp.salary_policy = "equal_monthly"
    emp.start_date = date(2025, 11, 20)
    emp.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    run = payrun.generate(
        session, emp, start=date(2025, 11, 1), end=date(2025, 11, 30), pay_date=date(2025, 11, 28)
    )
    assert run.gross_pence == 110000


def test_unpaid_leave_reduces_printed_gross_before_deductions(session, emp):
    run = prior(session, emp, status=PayrollRunStatus.forecast)
    payrun.reconcile(
        session,
        run,
        ({kind: 0 for kind in K} | {K.base_salary: 300000, K.tax: -30000, K.unpaid_leave: -10000}),
        payslip_gross_pence=290000,
        payslip_net_pence=260000,
        taxable_pence=290000,
    )
    assert payroll_totals.totals_for_run(run).actual_gross_pence == 290000


def test_reopen_preserves_original_verified_actuals_in_linked_correction(session, emp):
    original = prior(session, emp)
    original.verified_gross_pence = 300000
    original.verified_net_pence = 260000
    replacement = payrun.reopen(session, original)
    assert replacement.id != original.id and replacement.corrects_id == original.id
    assert original.status is PayrollRunStatus.corrected
    assert original.verified_net_pence == 260000
    assert original.components[1].actual_pence == -40000


def test_regular_estimate_uses_nominal_tax_year_and_code_for_early_payday(session, emp):
    from app.domain import schedule

    emp.payday_rule = '{"kind":"fixed_day","day":6,"adjust":"previous"}'
    emp.salary_policy = "equal_monthly"
    payroll.add_tax_code_revision(session, emp, date(2026, 4, 6), "BR")
    with patch.object(tax, "compute_period", wraps=tax.compute_period) as spy:
        schedule._employment_net_estimate(session, emp, date(2026, 4, 2))
    assert spy.call_args.kwargs["tax_code"] == "BR"
    expected = next(
        p["params"]
        for p in tax.embedded_rule_sets()
        if p["tax_year"] == "2026-27" and p["jurisdiction"] == "england_wales"
    )
    assert spy.call_args.kwargs["params"] == expected


def test_rebuild_preview_fingerprints_delivery_input_changes(session, emp):
    from app.persistence.models import DeliveryEntry

    emp.employment_type = "piecework"
    emp.deduction_mode = "gross_only"
    prior(session, emp, status=PayrollRunStatus.forecast)
    entry = DeliveryEntry(
        employment_id=emp.id,
        work_date=date(2025, 4, 10),
        hours=Decimal("4"),
        hourly_rate_pence=1500,
        deliveries=0,
        delivery_rate_pence=0,
        tips_pence=0,
    )
    session.add(entry)
    session.flush()
    preview = payrun.rebuild_forecasts(session, emp, apply=False)
    entry.hours = Decimal("5")
    with pytest.raises(PayrollError, match="changed"):
        payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
