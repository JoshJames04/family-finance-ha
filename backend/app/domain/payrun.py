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

import calendar
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import overtime as ot
from app.domain import pay_calendar, paydays, payroll_totals, tax
from app.domain import payroll as dom
from app.domain.payroll import PayrollError
from app.domain.tax import Deductions, PensionMode, TaxError
from app.persistence.models import (
    DeliveryEntry,
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
    allow_stale_rules: bool = False,
) -> PayrollRun:
    """Assemble a forecast run for a pay period."""
    if end < start:
        raise PayrollError("period end must be on/after start")
    if employment.end_date is not None:
        if start > employment.end_date:
            raise PayrollError(
                f"employment {employment.id} ended "
                f"{employment.end_date} — no run can start after it")
        if end > employment.end_date:   # final partial period
            end = employment.end_date
    if unpaid_leave_days is not None:
        unpaid_leave_pence += unpaid_leave_for_days(
            session, employment, unpaid_leave_days, end)
    if employment.employment_type == "piecework":
        entries = session.scalars(
            select(DeliveryEntry).where(
                DeliveryEntry.employment_id == employment.id,
                DeliveryEntry.work_date >= start,
                DeliveryEntry.work_date <= end,
                DeliveryEntry.source_transaction_id.is_(None))).all()
        base = sum(entry.pay_pence for entry in entries)
    else:
        base = dom.gross_for_period(session, employment.id, start, end)
    ot_pay = ot.approved_total_pence(session, employment.id, start, end)
    earnings = base + ot_pay + bonus_pence + allowance_pence
    taxable_gross = earnings - unpaid_leave_pence

    # Pension % applies on post-leave pensionable gross (issue #83).
    pension_pence = pension_pence_for(employment, taxable_gross)
    tax_year = tax.tax_year_for(pay_date)
    rules = None
    if employment.deduction_mode != "gross_only":
        try:
            rules = tax.rule_set(session, tax_year, employment.jurisdiction)
        except TaxError:
            # Forecasting beyond published HMRC tables: fall back to the
            # newest known rules; the run still records which version
            # computed it and can be regenerated when rates land.
            if not allow_stale_rules:
                raise
            fallback = tax.latest_rule_set(session, employment.jurisdiction)
            if fallback is None:
                raise
            rules = fallback
    params = json.loads(rules.params) if rules is not None else {}
    code = ("NT" if employment.deduction_mode == "gross_only"
            else dom.tax_code_at(session, employment.id, end))
    weekly = False
    if employment.payday_rule:
        try:
            weekly = pay_calendar.parse_rule(
                employment.payday_rule).kind.value == "weekly"
        except (ValueError, KeyError, TypeError):
            weekly = False
    nightly = False
    if employment.payday_rule:
        try:
            nightly = pay_calendar.parse_rule(
                employment.payday_rule).kind.value == "nightly"
        except (ValueError, KeyError, TypeError):
            nightly = False
    if nightly and employment.deduction_mode == "paye":
        raise PayrollError(
            "nightly PAYE requires weekly aggregation; choose gross-only "
            "for cash-in-hand work and record actual tax manually")
    if weekly:
        anchor = date(int(tax_year[:4]), 4, 6)
        tax_period = min(53, ((pay_date - anchor).days // 7) + 1)
    else:
        tax_period = tax.tax_month_for(pay_date)
    previous = session.scalars(
        select(PayrollRun).where(
            PayrollRun.employment_id == employment.id,
            PayrollRun.tax_year == tax_year,
            PayrollRun.pay_date < pay_date,
            PayrollRun.status != PayrollRunStatus.corrected)
        .order_by(PayrollRun.pay_date, PayrollRun.id)).all()
    superseded = {prior.corrects_id for prior in previous
                  if prior.corrects_id is not None}
    ytd_gross = 0
    ytd_tax = 0
    for prior in previous:
        if prior.id in superseded:
            continue
        values = prior.components
        source = (
            [c.actual_pence if c.actual_pence is not None else 0 for c in values]
            if prior.status is PayrollRunStatus.reconciled
            else [c.forecast_pence for c in values])
        taxable_kinds = EARNING | {PayrollComponentKind.unpaid_leave}
        taxable = sum(
            amount for component, amount in zip(values, source, strict=True)
            if component.kind in taxable_kinds)
        if employment.pension_mode.value in {"salary_sacrifice", "net_pay"}:
            taxable += sum(
                amount for component, amount in zip(values, source, strict=True)
                if component.kind is PayrollComponentKind.pension)
        if prior.status is PayrollRunStatus.reconciled:
            ytd_gross += taxable
            ytd_tax += -sum(
                c.actual_pence or 0 for c in values
                if c.kind is PayrollComponentKind.tax)
        else:
            ytd_gross += taxable
            ytd_tax += -sum(
                c.forecast_pence for c in values
                if c.kind is PayrollComponentKind.tax)
    if employment.deduction_mode == "gross_only":
        ded = Deductions(
            taxable_gross_pence=taxable_gross, tax_pence=0, ni_pence=0,
            student_loan_pence=0, pension_pence=pension_pence,
            net_pension_deduction_pence=(
                pension_pence
                if PensionMode(employment.pension_mode)
                is PensionMode.relief_at_source else 0))
    else:
        ded = tax.compute_period(
            gross_pence=taxable_gross, tax_code=code, params=params,
            period=tax_period, periods_per_year=52 if weekly else 12,
            ytd_taxable_gross_pence=ytd_gross,
            ytd_tax_pence=ytd_tax,
            ni_category=employment.ni_category,
            student_loan_plan=employment.student_loan_plan.value,
            pension_mode=PensionMode(employment.pension_mode),
            pension_pence=pension_pence)
    net = ded.net_pence

    run = PayrollRun(
        employment_id=employment.id, period_start=start,
        period_end=end, pay_date=pay_date,
        status=PayrollRunStatus.forecast, tax_year=tax_year,
        rule_set_year=rules.tax_year if rules is not None else None,
        rule_set_version=rules.version if rules is not None else 0,
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
    totals = payroll_totals.totals_for_run(run)
    new = PayrollRun(
        employment_id=run.employment_id, period_start=run.period_start,
        period_end=run.period_end, pay_date=run.pay_date,
        status=PayrollRunStatus.forecast, tax_year=run.tax_year,
        rule_set_year=run.rule_set_year,
        rule_set_version=run.rule_set_version,
        gross_pence=(totals.actual_gross_pence if totals.actual_gross_pence
                     is not None else run.gross_pence),
        net_pence=(totals.actual_net_pence if totals.actual_net_pence
                   is not None else run.net_pence),
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


def sync_piecework_run(
    session: Session, employment: Employment, work_date: date,
) -> tuple[PayrollRun | None, PayrollRun | None]:
    """(Re)build the forecast run covering a piecework work date.

    Delivery entries feed runs at generation time; this keeps them in
    step when entries are logged/edited/deleted (issue #305). Returns
    (new_run, superseded_run) — the caller rematerializes both into
    the expected-income schedule. The superseded forecast run is
    deleted (forecasts are disposable); a *reconciled* covering run is
    never touched — it raises PayrollError so the caller surfaces a
    correction path instead of silently losing pay."""
    if employment.employment_type != "piecework":
        return None, None
    rule = None
    if employment.payday_rule:
        try:
            rule = pay_calendar.parse_rule(employment.payday_rule)
        except (ValueError, KeyError, TypeError):
            rule = None
    if rule is not None and rule.kind.value == "nightly":
        start = end = pay_date = work_date
    elif rule is not None and rule.kind.value == "weekly":
        assert rule.weekday is not None
        pay_date = work_date + timedelta(
            days=(rule.weekday - work_date.weekday()) % 7)
        start, end = pay_date - timedelta(days=6), pay_date
    else:
        start = work_date.replace(day=1)
        end = work_date.replace(
            day=calendar.monthrange(work_date.year,
                                    work_date.month)[1])
        pay_date = (paydays.pay_date(rule, end.year, end.month)
                    if rule is not None else end)
    if employment.start_date is not None and start < employment.start_date:
        start = employment.start_date
    if employment.end_date is not None and end > employment.end_date:
        end = employment.end_date
    if end < start:
        return None, None

    has_work = session.scalar(select(DeliveryEntry.id).where(
        DeliveryEntry.employment_id == employment.id,
        DeliveryEntry.work_date >= start,
        DeliveryEntry.work_date <= end,
        DeliveryEntry.source_transaction_id.is_(None)).limit(1))
    run = session.scalars(select(PayrollRun).where(
        PayrollRun.employment_id == employment.id,
        PayrollRun.period_start == start,
        PayrollRun.period_end == end,
        PayrollRun.status != PayrollRunStatus.corrected)
        .order_by(PayrollRun.id)).first()
    if run is not None:
        if run.status is PayrollRunStatus.reconciled:
            raise PayrollError(
                f"{start}–{end} is already reconciled — issue a "
                "correction rather than changing logged work")
        if session.scalar(select(PayrollOverride.id).where(
                PayrollOverride.run_id == run.id).limit(1)) is not None:
            raise PayrollError(
                f"run {run.id} has manual overrides — correct it "
                "manually so the adjustments are not lost")
        run.status = PayrollRunStatus.corrected   # drop schedule rows
        session.delete(run)
        session.flush()
    if has_work is None:
        return None, run
    new = generate(session, employment, start=start, end=end,
                   pay_date=pay_date, allow_stale_rules=True)
    return new, run
