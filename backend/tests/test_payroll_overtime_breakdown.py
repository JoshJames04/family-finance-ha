"""Forecast lines retain approved hours, snapshotted rates and entry rounding."""

import json
from datetime import date
from decimal import Decimal

import pytest

from app.domain import overtime, payroll, payrun
from app.persistence.models import PayrollComponentKind
from tests import test_payrun as fixtures

emp = fixtures.emp


def approved(session, emp, day, hours, rate, multiplier=15_000):
    entry = overtime.create_entry(
        session, emp, work_date=day, hours=Decimal(hours),
        rate_pence=rate, multiplier_bps=multiplier,
    )
    overtime.submit(entry)
    overtime.approve(entry)
    return entry


def test_grouped_lines_sum_entry_pence_without_rerounding_or_rerating(session, emp):
    first = approved(session, emp, date(2025, 4, 4), '0.5', 1001)
    second = approved(session, emp, date(2025, 4, 5), '0.5', 1001)
    double = approved(session, emp, date(2025, 4, 6), '1', 1001, 20_000)
    payroll.add_salary_revision(session, emp, date(2025, 4, 10), 9_600_000)
    run = payrun.generate(session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30),
                          pay_date=date(2025, 4, 28))
    lines = [c for c in run.components if c.kind == PayrollComponentKind.overtime]
    assert [(c.label, c.forecast_pence) for c in lines] == [
        ('1 hour @ £15.015', 1500), ('1 hour @ £20.02', 2002),
    ]
    assert sum(c.forecast_pence for c in lines) == sum(e.pay_pence for e in (first, second, double))
    assert all(e.hourly_rate_pence == 1001 for e in (first, second, double))
    assert sum(c.forecast_pence for c in run.components) == run.net_pence


def test_equivalent_effective_rates_share_one_line(session, emp):
    approved(session, emp, date(2025, 4, 3), '2', 1800)
    approved(session, emp, date(2025, 4, 4), '3', 2700, 10_000)
    run = payrun.generate(session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30),
                          pay_date=date(2025, 4, 28))
    lines = [c for c in run.components if c.kind == PayrollComponentKind.overtime]
    assert [(c.label, c.forecast_pence) for c in lines] == [('5 hours @ £27', 13500)]


def test_cutoff_window_and_approval_status_control_breakdown(session, emp):
    emp.payday_rule = '{"cutoff_day":14,"pay_rule":{"kind":"fixed_day","day":28}}'
    emp.salary_policy = 'equal_monthly'
    payroll.add_tax_code_revision(session, emp, date(2025, 4, 1), '1257L M1')
    approved(session, emp, date(2025, 4, 14), '10', 1950)
    approved(session, emp, date(2025, 4, 15), '1', 1950)
    approved(session, emp, date(2025, 4, 30), '2', 1950)
    approved(session, emp, date(2025, 5, 14), '3', 1950)
    approved(session, emp, date(2025, 5, 15), '10', 1950)
    overtime.create_entry(session, emp, work_date=date(2025, 5, 10), hours=Decimal('10'))
    submitted = overtime.create_entry(
        session, emp, work_date=date(2025, 5, 12), hours=Decimal('10'),
    )
    overtime.submit(submitted)
    run = payrun.generate(session, emp, start=date(2025, 5, 1), end=date(2025, 5, 31),
                          pay_date=date(2025, 5, 28))
    lines = [c for c in run.components if c.kind == PayrollComponentKind.overtime]
    assert (run.period_start, run.period_end) == (date(2025, 4, 15), date(2025, 5, 14))
    assert [(c.label, c.forecast_pence) for c in lines] == [('6 hours @ £29.25', 17550)]


def test_legacy_overtime_display_marks_only_forecasts_for_automatic_refresh(session, emp):
    from app.persistence.models import PayrollRunStatus

    approved(session, emp, date(2025, 4, 3), '2', 1800)
    run = payrun.generate(session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30),
                          pay_date=date(2025, 4, 28))
    snapshot = json.loads(run.calculation_inputs)
    assert snapshot.pop('overtime_breakdown_version', None) == 1
    run.calculation_inputs = json.dumps(snapshot)
    payrun.flag_outdated_forecasts(session, emp)
    assert run.forecast_stale
    run.status = PayrollRunStatus.reconciled
    run.forecast_stale = False
    payrun.flag_outdated_forecasts(session, emp)
    assert not run.forecast_stale


@pytest.mark.parametrize('work_date, expected_stale', [
    (date(2025, 4, 3), True), (date(2025, 5, 3), False),
])
def test_legacy_zero_value_overtime_uses_approved_window_not_old_components(
    session, emp, work_date, expected_stale,
):
    approved(session, emp, work_date, '2', 0)
    run = payrun.generate(session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30),
                          pay_date=date(2025, 4, 28))
    # The previous generator omitted the component when overtime totalled zero.
    run.components[:] = [c for c in run.components if c.kind != PayrollComponentKind.overtime]
    snapshot = json.loads(run.calculation_inputs)
    snapshot.pop('overtime_breakdown_version')
    run.calculation_inputs = json.dumps(snapshot)
    payrun.flag_outdated_forecasts(session, emp)
    assert run.forecast_stale is expected_stale
