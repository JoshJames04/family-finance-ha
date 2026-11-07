"""Payroll runs (issue #82): forecast → reconciled, immutable except
via correction.

A run assembles salary proration (#78) + approved overtime (#79) +
bonus/allowance/unpaid-leave adjustments, then runs the versioned tax
engine (#81) with the employment's code/NI/plan config effective at
the period end. The rule-set version used is pinned on the run so a
later HMRC parameter bump never rewrites history. Forecast net feeds
Epic 7's expected-income schedule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import overtime as ot
from app.domain import payroll as dom
from app.domain import tax
from app.domain.payroll import PayrollError
from app.domain.tax import PensionMode
from app.persistence.models import (
    Employment,
    PayrollComponent,
    PayrollComponentKind,
    PayrollOverride,
    PayrollRun,
    PayrollRunStatus,
)

EARNING = {PayrollComponentKind.base_salary,
           PayrollComponentKind.overtime,
           PayrollComponentKind.bonus,
           PayrollComponentKind.allowance}
DEDUCTION = {PayrollComponentKind.unpaid_leave,
             PayrollComponentKind.pension,
             PayrollComponentKind.tax,
             PayrollComponentKind.ni,
             PayrollComponentKind.student_loan}


@dataclass(frozen=True)
class Variance:
    kind: PayrollComponentKind
    label: str
    forecast_pence: int
    actual_pence: int
    delta_pence: int


def _component(run: PayrollRun, kind: PayrollComponentKind,
               label: str, pence: int) -> None:
    run.components.append(PayrollComponent(
        kind=kind, label=label, forecast_pence=pence))


def unpaid_leave_for_days(session: Session, employment: Employment,
                          days: Decimal, on: date) -> int:
    """One unpaid day = the daily rate derived from salary and
    contracted-hours proration (annual / (52 × days-per-week))."""
    if days <= 0:
        raise PayrollError("unpaid leave days must be positive")
    annual = dom.salary_at(session, employment.id, on)
    daily = (Decimal(annual)
             / (Decimal(52) * Decimal(str(employment.work_days_per_week))))
    return int((daily * days).quantize(Decimal("1")))


def pension_pence_for(employment: Employment, pensionable_gross: int
                      ) -> int:
    """Employment-configured contribution: fixed amount wins over the
    percentage-of-gross rate."""
    if employment.pension_fixed_pence:
        return employment.pension_fixed_pence
    return pensionable_gross * employment.pension_employee_bps // 10_000


def generate(
    session: Session, employment: Employment, *,
    start: date, end: date, pay_date: date,
    bonus_pence: int = 0, allowance_pence: int = 0,
    unpaid_leave_pence: int = 0,
    unpaid_leave_days: Decimal | None = None,
) -> PayrollRun:
    """Assemble a forecast run for a pay period."""
    if end < start:
        raise PayrollError("period end must be on/after start")
    if unpaid_leave_days is not None:
        unpaid_leave_pence += unpaid_leave_for_days(
            session, employment, unpaid_leave_days, end)
    base = dom.gross_for_period(session, employment.id, start, end)
    ot_pay = ot.approved_total_pence(session, employment.id, start, end)
    earnings = base + ot_pay + bonus_pence + allowance_pence
    taxable_gross = earnings - unpaid_leave_pence

    # Pension % applies on post-leave pensionable gross (issue #83).
    pension_pence = pension_pence_for(employment, taxable_gross)
    tax_year = tax.tax_year_for(end)
    rules = tax.rule_set(session, tax_year, employment.jurisdiction)
    params = json.loads(rules.params)
    code = dom.tax_code_at(session, employment.id, end)
    ded = tax.compute_period(
        gross_pence=taxable_gross, tax_code=code, params=params,
        period=tax.tax_month_for(end),
        ni_category=employment.ni_category,
        student_loan_plan=employment.student_loan_plan.value,
        pension_mode=PensionMode(employment.pension_mode),
        pension_pence=pension_pence)
    net = ded.net_pence

    run = PayrollRun(
        employment_id=employment.id, period_start=start,
        period_end=end, pay_date=pay_date,
        status=PayrollRunStatus.forecast, tax_year=tax_year,
        rule_set_version=rules.version,
        gross_pence=earnings, net_pence=net)
    _component(run, PayrollComponentKind.base_salary,
               "Base salary", base)
    if ot_pay:
        _component(run, PayrollComponentKind.overtime,
                   "Approved overtime", ot_pay)
    if bonus_pence:
        _component(run, PayrollComponentKind.bonus, "Bonus",
                   bonus_pence)
    if allowance_pence:
        _component(run, PayrollComponentKind.allowance,
                   "Allowance", allowance_pence)
    if unpaid_leave_pence:
        _component(run, PayrollComponentKind.unpaid_leave,
                   "Unpaid leave", -unpaid_leave_pence)
    if pension_pence:
        _component(run, PayrollComponentKind.pension,
                   f"Pension ({PensionMode(employment.pension_mode).value})",
                   -pension_pence)
    _component(run, PayrollComponentKind.tax, "PAYE income tax",
               -ded.tax_pence)
    _component(run, PayrollComponentKind.ni, "National Insurance",
               -ded.ni_pence)
    if ded.student_loan_pence:
        _component(run, PayrollComponentKind.student_loan,
                   f"Student loan ({employment.student_loan_plan.value})",
                   -ded.student_loan_pence)
    session.add(run)
    session.flush()
    return run


def reconcile(
    session: Session, run: PayrollRun,
    actuals: dict[PayrollComponentKind, int],
) -> list[Variance]:
    """Enter the payslip's actual amounts keyed by component kind.

    Any kind absent from `actuals` is assumed correct (actual =
    forecast). Returns the component-level variance breakdown. A
    reconciled run is immutable — use `correct()` instead."""
    _require_forecast(run)
    variances: list[Variance] = []
    for c in run.components:
        actual = actuals.get(c.kind, c.forecast_pence)
        c.actual_pence = actual
        if actual != c.forecast_pence:
            variances.append(Variance(
                kind=c.kind, label=c.label,
                forecast_pence=c.forecast_pence,
                actual_pence=actual,
                delta_pence=actual - c.forecast_pence))
    # Actuals for kinds not on the forecast (e.g. an unexpected bonus)
    known = {c.kind for c in run.components}
    for kind, actual in actuals.items():
        if kind not in known:
            run.components.append(PayrollComponent(
                kind=kind, label=kind.value.replace("_", " ").title(),
                forecast_pence=0, actual_pence=actual))
            variances.append(Variance(
                kind=kind, label=kind.value,
                forecast_pence=0, actual_pence=actual,
                delta_pence=actual))
    run.status = PayrollRunStatus.reconciled
    session.flush()
    return variances


def component_variance(run: PayrollRun) -> list[Variance]:
    """The divergence breakdown a reconciled payslip surfaces."""
    return [Variance(kind=c.kind, label=c.label,
                     forecast_pence=c.forecast_pence,
                     actual_pence=c.actual_pence,
                     delta_pence=c.variance_pence or 0)
            for c in run.components
            if c.actual_pence is not None
            and c.variance_pence != 0]


def correct(session: Session, run: PayrollRun) -> PayrollRun:
    """A reconciled run can only be superseded: mark it corrected and
    spawn a fresh forecast run over the same period."""
    if run.status is not PayrollRunStatus.reconciled:
        raise PayrollError("only a reconciled run can be corrected")
    run.status = PayrollRunStatus.corrected
    new = PayrollRun(
        employment_id=run.employment_id, period_start=run.period_start,
        period_end=run.period_end, pay_date=run.pay_date,
        status=PayrollRunStatus.forecast, tax_year=run.tax_year,
        rule_set_version=run.rule_set_version,
        gross_pence=run.gross_pence, net_pence=run.net_pence,
        corrects_id=run.id)
    session.add(new)
    for c in run.components:
        new.components.append(PayrollComponent(
            kind=c.kind, label=c.label,
            forecast_pence=c.actual_pence
            if c.actual_pence is not None else c.forecast_pence))
    session.flush()
    return new


def _require_forecast(run: PayrollRun) -> None:
    if run.status is not PayrollRunStatus.forecast:
        raise PayrollError(
            f"payroll run {run.id} is {run.status} — reconciled runs "
            "are immutable; issue a correction instead")


def runs_for_employment(session: Session, employment_id: int
                       ) -> list[PayrollRun]:
    return list(session.execute(
        select(PayrollRun).where(
            PayrollRun.employment_id == employment_id)
        .order_by(PayrollRun.period_start, PayrollRun.id)
    ).scalars().all())


def apply_override(
    session: Session, run: PayrollRun, *,
    kind: PayrollComponentKind, delta_pence: int,
    reason: str, actor: str,
) -> PayrollOverride:
    """Append a run-scoped manual adjustment (issue #85).

    The override is a distinct component line (label carries the
    reason) plus an auditable ``payroll_override`` row. Deltas on
    earnings kinds move gross; every delta moves net by its own signed
    amount. Reconciled runs must be re-opened first; corrected runs
    are immutable forever.
    """
    if run.status is PayrollRunStatus.corrected:
        raise PayrollError(
            f"payroll run {run.id} is corrected — immutable")
    if run.status is PayrollRunStatus.reconciled:
        raise PayrollError(
            f"payroll run {run.id} is reconciled — re-open it before "
            "applying an override")
    if delta_pence == 0:
        raise PayrollError("override delta must be non-zero")
    if not reason.strip():
        raise PayrollError("override requires a reason")
    ov = PayrollOverride(run_id=run.id, kind=kind,
                         delta_pence=delta_pence,
                         reason=reason.strip(), actor=actor)
    session.add(ov)
    run.components.append(PayrollComponent(
        kind=kind, label=f"Manual override — {reason.strip()}",
        forecast_pence=delta_pence))
    if kind in EARNING:
        run.gross_pence += delta_pence
    run.net_pence += delta_pence
    session.flush()
    return ov


def reopen(session: Session, run: PayrollRun) -> PayrollRun:
    """Re-open a reconciled run for manual adjustment (issue #85).

    Actuals are preserved — they are data, not state; the run becomes
    mutable again so an override can be applied, after which it can be
    reconciled again. Corrected runs cannot re-open."""
    if run.status is PayrollRunStatus.corrected:
        raise PayrollError(
            f"payroll run {run.id} is corrected — immutable")
    if run.status is not PayrollRunStatus.reconciled:
        raise PayrollError(
            f"payroll run {run.id} is {run.status} — only a "
            "reconciled run needs re-opening")
    run.status = PayrollRunStatus.forecast
    session.flush()
    return run
