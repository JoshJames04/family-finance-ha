from app.domain.payroll_totals import totals_for_run
from app.persistence.models import PayrollComponent, PayrollRun, PayrollRunStatus
from app.persistence.models import PayrollComponentKind as Kind


def make_run() -> PayrollRun:
    return PayrollRun(
        employment_id=1, gross_pence=300_000, net_pence=240_000,
        status=PayrollRunStatus.reconciled)


def test_partial_import_preserves_authoritative_totals() -> None:
    run = make_run()
    run.components = [
        PayrollComponent(kind=Kind.overtime, forecast_pence=10_000,
                         actual_pence=10_000),
        PayrollComponent(kind=Kind.tax, forecast_pence=20_000,
                         actual_pence=20_000),
    ]
    totals = totals_for_run(run)
    assert totals.actual_gross_pence == 300_000
    assert totals.actual_net_pence == 240_000
    run.components[1].actual_pence = 21_000
    assert totals_for_run(run).actual_net_pence == 239_000


def test_native_signed_deductions_and_zero_actuals() -> None:
    run = make_run()
    run.components = [
        PayrollComponent(kind=Kind.base_salary, forecast_pence=300_000,
                         actual_pence=300_000),
        PayrollComponent(kind=Kind.tax, forecast_pence=-60_000,
                         actual_pence=-61_000),
    ]
    assert totals_for_run(run).actual_net_pence == 239_000
    for component in run.components:
        component.actual_pence = 0
    totals = totals_for_run(run)
    assert totals.actual_gross_pence == 0
    assert totals.actual_net_pence == 0


def test_forecasts_do_not_present_actuals() -> None:
    run = make_run()
    run.status = PayrollRunStatus.forecast
    totals = totals_for_run(run)
    assert totals.forecast_gross_pence == 300_000
    assert totals.forecast_net_pence == 240_000
    assert totals.actual_gross_pence is None
    assert totals.actual_net_pence is None
