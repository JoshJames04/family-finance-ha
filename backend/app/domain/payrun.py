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

from app.domain import outbox, pay_calendar, paydays, payroll_totals, tax
from app.domain import overtime as ot
from app.domain import payroll as dom
from app.domain.payroll import PayrollError
from app.domain.payroll_rebuild import rebuild_forecasts as rebuild_forecasts
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

EARNING = {
    PayrollComponentKind.base_salary,
    PayrollComponentKind.overtime,
    PayrollComponentKind.bonus,
    PayrollComponentKind.allowance,
}
DEDUCTION = {
    PayrollComponentKind.unpaid_leave,
    PayrollComponentKind.pension,
    PayrollComponentKind.tax,
    PayrollComponentKind.ni,
    PayrollComponentKind.student_loan,
}


@dataclass(frozen=True)
class Variance:
    kind: PayrollComponentKind
    label: str
    forecast_pence: int
    actual_pence: int
    delta_pence: int


def _component(run: PayrollRun, kind: PayrollComponentKind, label: str, pence: int) -> None:
    run.components.append(PayrollComponent(kind=kind, label=label, forecast_pence=pence))


def unpaid_leave_for_days(session: Session, employment: Employment, days: Decimal, on: date) -> int:
    """One unpaid day = the daily rate derived from salary and
    contracted-hours proration (annual / (52 × days-per-week))."""
    if days <= 0:
        raise PayrollError("unpaid leave days must be positive")
    annual = dom.salary_at(session, employment.id, on)
    daily = Decimal(annual) / (Decimal(52) * Decimal(str(employment.work_days_per_week)))
    return int((daily * days).quantize(Decimal("1")))


def pension_pence_for(employment: Employment, pensionable_gross: int) -> int:
    """Employment-configured contribution: fixed amount wins over the
    percentage-of-gross rate."""
    if PensionMode(employment.pension_mode) is PensionMode.none:
        return 0
    if employment.pension_fixed_pence:
        return employment.pension_fixed_pence
    return pensionable_gross * employment.pension_employee_bps // 10_000


def _emit_transition(session: Session, run: PayrollRun, transition: str) -> None:
    outbox.emit(
        session,
        run.employment.household_id,
        f"payroll.{transition}",
        entity_kind="payroll_run",
        entity_id=run.id,
        payload={
            "source": "payroll",
            "status": run.status.value,
            "date": run.pay_date.isoformat(),
            "owner_profile_id": run.employment.profile_id,
        },
        dedup_key=f"payroll:{run.id}:{transition}",
    )


def statutory_date_for(employment: Employment, cash_date: date, label_month: date) -> date:
    """CWG2 regular-payday exception only when cash matches the configured shift.
    Irregular payments retain their actual date. Never move the bank transaction.
    """
    if employment.payday_rule:
        try:
            rule = pay_calendar.parse_rule(employment.payday_rule)
        except (ValueError, TypeError, KeyError) as exc:
            raise PayrollError("configure a valid payday rule before generating payroll") from exc
        if rule.kind is paydays.RuleKind.weekly:
            for offset in range(-7, 8):
                nominal = cash_date + timedelta(days=offset)
                if (
                    nominal.weekday() == rule.weekday
                    and paydays.adjust(nominal, rule.adjust) == cash_date
                ):
                    return nominal
        elif rule.kind is not paydays.RuleKind.nightly:
            nominal = paydays.nominal_date(rule, label_month.year, label_month.month)
            if paydays.pay_date(rule, label_month.year, label_month.month) == cash_date:
                return nominal
    return cash_date


def effective_pay_date(employment: Employment, run: PayrollRun) -> date:
    """Only unconfirmed ordinary forecasts may derive dates from today's rule.
    Recorded actuals and pending corrections need their own evidenced PAYE date.
    """
    if run.statutory_pay_date is not None:
        return run.statutory_pay_date
    if (
        run.status is PayrollRunStatus.forecast
        and run.corrects_id is None
        and not any(c.actual_pence is not None for c in run.components)
    ):
        return statutory_date_for(employment, run.pay_date, run.period_end)
    return run.pay_date


def flag_outdated_forecasts(session: Session, employment: Employment) -> None:
    """Upgrades invalidate estimates, never rewrite confirmed history or amounts."""
    for run in session.scalars(
        select(PayrollRun).where(
            PayrollRun.employment_id == employment.id,
            PayrollRun.status == PayrollRunStatus.forecast,
        )
    ):
        try:
            snapshot = json.loads(run.calculation_inputs or "{}")
        except (ValueError, TypeError):
            snapshot = {}
        if snapshot.get("algorithm") != tax.CALCULATION_VERSION:
            run.forecast_stale = True
            continue
        if employment.deduction_mode == "gross_only":
            continue
        try:
            rules = tax.rule_set(
                session,
                tax.tax_year_for(effective_pay_date(employment, run)),
                employment.jurisdiction,
            )
        except TaxError:
            fallback = tax.latest_rule_set(
                session,
                employment.jurisdiction,
                not_after=tax.tax_year_for(effective_pay_date(employment, run)),
            )
            if fallback is None:
                continue
            rules = fallback
        if run.rule_set_year != rules.tax_year or run.rule_set_version != rules.version:
            run.forecast_stale = True


def prior_ytd(session: Session, employment: Employment, payday: date) -> tuple[int, int]:
    flag_outdated_forecasts(session, employment)
    anchor = date(int(tax.tax_year_for(payday)[:4]), 4, 6)
    rows = list(
        session.scalars(
            select(PayrollRun)
            .where(
                PayrollRun.employment_id == employment.id,
                PayrollRun.status != PayrollRunStatus.corrected,
            )
            .order_by(PayrollRun.pay_date, PayrollRun.id)
        )
    )
    target_year = tax.tax_year_for(payday)
    later_anchor = max(
        (
            effective_pay_date(employment, r)
            for r in rows
            if r.status is PayrollRunStatus.reconciled
            and r.verified_ytd_taxable_pence is not None
            and r.verified_ytd_tax_pence is not None
            and anchor <= (effective_pay_date(employment, r)) < payday
        ),
        default=None,
    )
    for r in rows:
        if r.statutory_pay_date is None and r.status is PayrollRunStatus.reconciled:
            label_year = f"{int(r.tax_year[:4])}-{str(int(r.tax_year[:4]) + 1)[2:]}"
            cash_year = tax.tax_year_for(r.pay_date)
            if (
                label_year != cash_year
                and target_year in {label_year, cash_year}
                and not (later_anchor is not None and r.pay_date < later_anchor)
            ):
                raise PayrollError(
                    "verify the historical statutory PAYE date; cash date and tax year conflict"
                )
    rows = sorted(
        (r for r in rows if anchor <= (effective_pay_date(employment, r)) < payday),
        key=lambda r: (effective_pay_date(employment, r), r.id),
    )
    # A verified cumulative payslip includes all earlier earnings, including
    # months absent from imported history. Do not add those months twice.
    anchor_index = next(
        (
            i
            for i in range(len(rows) - 1, -1, -1)
            if rows[i].status is PayrollRunStatus.reconciled
            and rows[i].verified_ytd_taxable_pence is not None
            and rows[i].verified_ytd_tax_pence is not None
        ),
        None,
    )
    covered_rows = list(rows)
    opening_date = None
    gross, paid = 0, 0
    if anchor_index is not None:
        opening = rows[anchor_index]
        opening_date = effective_pay_date(employment, opening)
        gross, paid = opening.verified_ytd_taxable_pence or 0, opening.verified_ytd_tax_pence or 0
        rows = rows[anchor_index + 1 :]
    for prior in rows:
        if prior.status is PayrollRunStatus.reconciled:
            if prior.verified_taxable_pence is None:
                raise PayrollError(
                    "prior payroll needs verified taxable pay or payslip YTD before forecasting"
                )
            if not any(
                c.kind is PayrollComponentKind.tax and c.actual_pence is not None
                for c in prior.components
            ):
                raise PayrollError(
                    "prior payroll needs verified PAYE actuals or paired payslip YTD"
                )
            gross += prior.verified_taxable_pence
            paid -= sum(
                payroll_totals.signed_component(
                    c.kind,
                    c.actual_pence,
                    native=(
                        prior.signed_components
                        if prior.signed_components is not None
                        else (prior.rule_set_version or 0) > 0
                    ),
                )
                for c in prior.components
                if c.kind is PayrollComponentKind.tax and c.actual_pence is not None
            )
        else:
            if prior.forecast_stale:
                raise PayrollError("prior forecast is stale; review and rebuild it first")
            snapshot = json.loads(prior.calculation_inputs or "{}")
            if "taxable_pence" not in snapshot:
                raise PayrollError(
                    "prior forecast lacks verified taxable inputs; review and rebuild it first"
                )
            gross += int(snapshot["taxable_pence"])
            paid -= sum(
                c.forecast_pence for c in prior.components if c.kind is PayrollComponentKind.tax
            )
    _require_ytd_coverage(session, employment, payday, covered_rows, opening_date)
    return gross, paid


def _require_ytd_coverage(
    session: Session,
    employment: Employment,
    payday: date,
    rows: list[PayrollRun],
    opening: date | None,
) -> None:
    """A missing scheduled payroll is unknown, never a zero-pay month/week."""
    anchor = date(int(tax.tax_year_for(payday)[:4]), 4, 6)
    rule = (
        pay_calendar.parse_rule(employment.payday_rule)
        if employment.payday_rule
        else paydays.PaydayRule(
            kind=paydays.RuleKind.fixed_day, day=payday.day, adjust=paydays.ShiftPolicy.none
        )
    )
    weekly = rule.kind is paydays.RuleKind.weekly

    def period(d: date) -> int:
        return ((d - anchor).days // 7) + 1 if weekly else tax.tax_month_for(d)

    if opening is not None:
        first = period(opening) + 1
    else:
        employed = employment.start_date
        if employed is None:
            employed = min((r.effective_date for r in employment.salary_revisions), default=anchor)
        beginning = max(anchor, employed)
        if weekly:
            assert rule.weekday is not None
            first_nominal = beginning + timedelta(days=(rule.weekday - beginning.weekday()) % 7)
        else:
            label = beginning.replace(day=1)
            first_nominal = paydays.nominal_date(rule, label.year, label.month)
            if first_nominal < beginning:
                label = label.replace(
                    day=calendar.monthrange(label.year, label.month)[1]
                ) + timedelta(days=1)
                first_nominal = paydays.nominal_date(rule, label.year, label.month)
        first = max(1, period(first_nominal))
    covered = {period(effective_pay_date(employment, r)) for r in rows}
    if set(range(first, period(payday))) - covered:
        raise PayrollError(
            "missing regular payroll periods: enter verified payslip YTD or complete the gap first"
        )


def generate(
    session: Session,
    employment: Employment,
    *,
    start: date,
    end: date,
    pay_date: date,
    bonus_pence: int = 0,
    allowance_pence: int = 0,
    unpaid_leave_pence: int = 0,
    unpaid_leave_days: Decimal | None = None,
    allow_stale_rules: bool = False,
    emit_event: bool = True,
    replacement_for: int | None = None,
) -> PayrollRun:
    """Assemble a forecast run for a pay period."""
    if end < start:
        raise PayrollError("period end must be on/after start")
    label_month = end.replace(day=1)
    salary_start, salary_end = start, end
    statutory_date = statutory_date_for(employment, pay_date, label_month)
    if employment.payday_rule and "cutoff_day" in employment.payday_rule:
        cal = pay_calendar.parse_calendar(employment.payday_rule)
        # The caller's period end labels the payroll month; bank-day shifts
        # can move the payment date into the previous or following month.
        window = pay_calendar.period(cal, end.year, end.month)
        start, end = window.start, window.cutoff
        if employment.salary_policy == "equal_monthly":
            salary_start = label_month
            salary_end = label_month.replace(
                day=calendar.monthrange(label_month.year, label_month.month)[1]
            )
        else:
            salary_start, salary_end = start, end
        if employment.start_date is not None and not (
            employment.salary_policy == "equal_monthly" and employment.start_date > end
        ):
            start = max(start, employment.start_date)
    if employment.end_date is not None:
        if start > employment.end_date:
            raise PayrollError(
                f"employment {employment.id} ended "
                f"{employment.end_date} — no run can start after it"
            )
        if end > employment.end_date:  # final partial period
            end = employment.end_date
    existing = session.scalars(
        select(PayrollRun)
        .where(
            PayrollRun.employment_id == employment.id,
            PayrollRun.period_start == start,
            PayrollRun.period_end == end,
            PayrollRun.status != PayrollRunStatus.corrected,
        )
        .order_by(PayrollRun.id.desc())
    ).first()
    if existing is not None:
        return existing
    fixed_leave_pence = unpaid_leave_pence
    if unpaid_leave_days is not None:
        unpaid_leave_pence += unpaid_leave_for_days(session, employment, unpaid_leave_days, end)
    if employment.employment_type == "piecework":
        entries = session.scalars(
            select(DeliveryEntry).where(
                DeliveryEntry.employment_id == employment.id,
                DeliveryEntry.work_date >= start,
                DeliveryEntry.work_date <= end,
                DeliveryEntry.source_transaction_id.is_(None),
            )
        ).all()
        base = sum(entry.pay_pence for entry in entries)
    else:
        if employment.start_date is not None:
            salary_start = max(salary_start, employment.start_date)
        if employment.end_date is not None:
            salary_end = min(salary_end, employment.end_date)
        base = (
            0
            if employment.salary_policy == "equal_monthly" and salary_end < salary_start
            else dom.gross_for_period(session, employment.id, salary_start, salary_end)
        )
    ot_pay = ot.approved_total_pence(session, employment.id, start, end)
    earnings = base + ot_pay + bonus_pence + allowance_pence
    taxable_gross = earnings - unpaid_leave_pence

    # Pension % applies on post-leave pensionable gross (issue #83).
    pension_pence = pension_pence_for(employment, taxable_gross)
    if not 0 <= pension_pence <= taxable_gross:
        raise PayrollError("pension cannot exceed pensionable earnings")
    tax_year = tax.tax_year_for(statutory_date)
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
            fallback = tax.latest_rule_set(session, employment.jurisdiction, not_after=tax_year)
            if fallback is None or fallback.tax_year > tax_year:
                raise
            rules = fallback
    params = json.loads(rules.params) if rules is not None else {}
    code = (
        "NT"
        if employment.deduction_mode == "gross_only"
        else dom.tax_code_at(session, employment.id, statutory_date)
    )
    weekly = False
    if employment.payday_rule:
        try:
            weekly = pay_calendar.parse_rule(employment.payday_rule).kind.value == "weekly"
        except (ValueError, KeyError, TypeError):
            weekly = False
    nightly = False
    if employment.payday_rule:
        try:
            nightly = pay_calendar.parse_rule(employment.payday_rule).kind.value == "nightly"
        except (ValueError, KeyError, TypeError):
            nightly = False
    if nightly and employment.deduction_mode == "paye":
        raise PayrollError(
            "nightly PAYE requires weekly aggregation; choose gross-only "
            "for cash-in-hand work and record actual tax manually"
        )
    if weekly:
        anchor = date(int(tax_year[:4]), 4, 6)
        tax_period = min(53, ((statutory_date - anchor).days // 7) + 1)
    else:
        tax_period = tax.tax_month_for(statutory_date)
    cumulative = (
        employment.deduction_mode != "gross_only"
        and tax.parse_tax_code(code).cumulative
        and not (weekly and tax_period == 53)
    )
    ytd_gross, ytd_tax = prior_ytd(session, employment, statutory_date) if cumulative else (0, 0)
    if employment.deduction_mode == "gross_only":
        ded = Deductions(
            taxable_gross_pence=(
                taxable_gross - pension_pence
                if PensionMode(employment.pension_mode)
                in {PensionMode.net_pay, PensionMode.salary_sacrifice}
                else taxable_gross
            ),
            tax_pence=0,
            ni_pence=0,
            student_loan_pence=0,
            pension_pence=pension_pence,
            net_pension_deduction_pence=(
                pension_pence
                if PensionMode(employment.pension_mode) is PensionMode.relief_at_source
                else 0
            ),
        )
    else:
        ded = tax.compute_period(
            gross_pence=taxable_gross,
            tax_code=code,
            params=params,
            period=tax_period,
            periods_per_year=52 if weekly else 12,
            ytd_taxable_gross_pence=ytd_gross,
            ytd_tax_pence=ytd_tax,
            ni_category=employment.ni_category,
            student_loan_plan=employment.student_loan_plan.value,
            pension_mode=PensionMode(employment.pension_mode),
            pension_pence=pension_pence,
        )
    net = ded.net_pence

    run = PayrollRun(
        employment_id=employment.id,
        corrects_id=replacement_for,
        period_start=start,
        period_end=end,
        pay_date=pay_date,
        status=PayrollRunStatus.forecast,
        tax_year=tax_year,
        rule_set_year=rules.tax_year if rules is not None else None,
        rule_set_version=rules.version if rules is not None else 0,
        gross_pence=earnings,
        net_pence=net,
        statutory_pay_date=statutory_date,
        signed_components=True,
        calculation_inputs=json.dumps(
            {
                "algorithm": tax.CALCULATION_VERSION,
                "payday_rule": employment.payday_rule,
                "payroll_month": label_month.isoformat(),
                "unpaid_leave_days": str(unpaid_leave_days)
                if unpaid_leave_days is not None
                else None,
                "unpaid_leave_fixed_pence": fixed_leave_pence,
                "taxable_pence": ded.taxable_gross_pence,
                "pension_mode": PensionMode(employment.pension_mode).value,
                "tax_code": code,
                "ni_category": employment.ni_category,
                "student_loan_plan": employment.student_loan_plan.value,
                "salary_policy": employment.salary_policy,
                "ytd_taxable_pence": ytd_gross,
                "ytd_tax_pence": ytd_tax,
                "statutory_pay_date": statutory_date.isoformat(),
                "overtime_start": start.isoformat(),
                "overtime_end": end.isoformat(),
            },
            sort_keys=True,
        ),
    )
    _component(run, PayrollComponentKind.base_salary, "Base salary", base)
    if ot_pay:
        _component(run, PayrollComponentKind.overtime, "Approved overtime", ot_pay)
    if bonus_pence:
        _component(run, PayrollComponentKind.bonus, "Bonus", bonus_pence)
    if allowance_pence:
        _component(run, PayrollComponentKind.allowance, "Allowance", allowance_pence)
    if unpaid_leave_pence:
        _component(run, PayrollComponentKind.unpaid_leave, "Unpaid leave", -unpaid_leave_pence)
    if pension_pence:
        _component(
            run,
            PayrollComponentKind.pension,
            f"Pension ({PensionMode(employment.pension_mode).value})",
            -pension_pence,
        )
    _component(run, PayrollComponentKind.tax, "PAYE income tax", -ded.tax_pence)
    _component(run, PayrollComponentKind.ni, "National Insurance", -ded.ni_pence)
    if ded.student_loan_pence:
        _component(
            run,
            PayrollComponentKind.student_loan,
            f"Student loan ({employment.student_loan_plan.value})",
            -ded.student_loan_pence,
        )
    session.add(run)
    session.flush()
    mark_later_forecasts_stale(session, run)
    if emit_event:
        _emit_transition(session, run, "generated")
    return run


def reconcile(
    session: Session,
    run: PayrollRun,
    actuals: dict[PayrollComponentKind, int],
    *,
    payslip_gross_pence: int | None = None,
    payslip_net_pence: int | None = None,
    taxable_pence: int | None = None,
    niable_pence: int | None = None,
    ytd_taxable_pence: int | None = None,
    ytd_tax_pence: int | None = None,
    statutory_pay_date: date | None = None,
) -> list[Variance]:
    """Enter the payslip's actual amounts keyed by component kind.

    Any kind absent from `actuals` is assumed correct (actual =
    forecast). Returns the component-level variance breakdown. A
    reconciled run is immutable — use `correct()` instead."""
    _require_forecast(run)
    if payslip_gross_pence is None or payslip_net_pence is None:
        raise PayrollError("enter independent payslip gross and net totals")
    if statutory_pay_date is not None and abs((statutory_pay_date - run.pay_date).days) > 31:
        raise PayrollError("statutory PAYE date must be within 31 days of the cash date")
    known = {c.kind for c in run.components}
    if not actuals or not set(PayrollComponentKind) <= actuals.keys():
        raise PayrollError("enter every actual component explicitly, including zero values")
    if run.employment.deduction_mode != "gross_only" and taxable_pence is None:
        raise PayrollError("enter verified taxable pay from the payslip")
    if (ytd_taxable_pence is None) != (ytd_tax_pence is None):
        raise PayrollError("enter both payslip YTD taxable pay and YTD PAYE, or neither")
    if any(v is not None and v < 0 for v in (taxable_pence, niable_pence, ytd_taxable_pence)):
        raise PayrollError("taxable and NI earnings cannot be negative")
    native = True  # This API contract is signed, independently of legacy rate metadata.
    signed = {k: payroll_totals.signed_component(k, v, native=native) for k, v in actuals.items()}
    gross = sum(v for k, v in signed.items() if k in EARNING) + signed.get(
        PayrollComponentKind.unpaid_leave, 0
    )
    # Some payslips report returned employee pension contributions in gross
    # additions. Their taxable basis remains the independently entered fact.
    refunded_pension = max(0, signed.get(PayrollComponentKind.pension, 0))
    if sum(signed.values()) != payslip_net_pence or payslip_gross_pence not in {
        gross,
        gross + refunded_pension,
    }:
        raise PayrollError("actual components must balance to the payslip gross and net totals")
    variances: list[Variance] = []
    seen: set[PayrollComponentKind] = set()
    # The API accepts an aggregate per kind. Allocate it ONCE to the first
    # line, zero the remaining lines, preserving line identity and variance.
    for c in sorted(run.components, key=lambda c: c.id):
        actual = actuals[c.kind] if c.kind not in seen else 0
        seen.add(c.kind)
        c.actual_pence = actual
        if actual != c.forecast_pence:
            variances.append(
                Variance(
                    kind=c.kind,
                    label=c.label,
                    forecast_pence=c.forecast_pence,
                    actual_pence=actual,
                    delta_pence=actual - c.forecast_pence,
                )
            )
    for kind, actual in actuals.items():
        if kind not in known:
            run.components.append(
                PayrollComponent(
                    kind=kind,
                    label=kind.value.replace("_", " ").title(),
                    forecast_pence=0,
                    actual_pence=actual,
                )
            )
            if actual:
                variances.append(
                    Variance(
                        kind=kind,
                        label=kind.value,
                        forecast_pence=0,
                        actual_pence=actual,
                        delta_pence=actual,
                    )
                )
    if statutory_pay_date is not None:
        run.statutory_pay_date = statutory_pay_date
        run.tax_year = tax.tax_year_for(statutory_pay_date)
    run.signed_components = True
    run.verified_gross_pence = payslip_gross_pence
    run.verified_net_pence = payslip_net_pence
    run.verified_taxable_pence = taxable_pence
    run.verified_niable_pence = niable_pence
    run.verified_ytd_taxable_pence = ytd_taxable_pence
    run.verified_ytd_tax_pence = ytd_tax_pence
    run.status = PayrollRunStatus.reconciled
    run.forecast_stale = False
    mark_later_forecasts_stale(session, run)
    session.flush()
    _emit_transition(session, run, "reconciled")
    return variances


def mark_later_forecasts_stale(session: Session, run: PayrollRun) -> None:
    payday = effective_pay_date(run.employment, run)
    for later in session.scalars(
        select(PayrollRun).where(
            PayrollRun.employment_id == run.employment_id,
            PayrollRun.status == PayrollRunStatus.forecast,
        )
    ):
        if effective_pay_date(run.employment, later) > payday:
            later.forecast_stale = True


def component_variance(run: PayrollRun) -> list[Variance]:
    """The divergence breakdown a reconciled payslip surfaces."""
    return [
        Variance(
            kind=c.kind,
            label=c.label,
            forecast_pence=c.forecast_pence,
            actual_pence=c.actual_pence,
            delta_pence=c.variance_pence or 0,
        )
        for c in run.components
        if c.actual_pence is not None and c.variance_pence != 0
    ]


def correct(session: Session, run: PayrollRun) -> PayrollRun:
    """A reconciled run can only be superseded: mark it corrected and
    spawn a fresh forecast run over the same period."""
    if run.status is not PayrollRunStatus.reconciled:
        raise PayrollError("only a reconciled run can be corrected")
    run.status = PayrollRunStatus.corrected
    mark_later_forecasts_stale(session, run)
    totals = payroll_totals.totals_for_run(run)
    new = PayrollRun(
        employment_id=run.employment_id,
        period_start=run.period_start,
        period_end=run.period_end,
        pay_date=run.pay_date,
        status=PayrollRunStatus.forecast,
        tax_year=run.tax_year,
        rule_set_year=run.rule_set_year,
        rule_set_version=run.rule_set_version,
        gross_pence=(
            totals.actual_gross_pence if totals.actual_gross_pence is not None else run.gross_pence
        ),
        net_pence=(
            totals.actual_net_pence if totals.actual_net_pence is not None else run.net_pence
        ),
        corrects_id=run.id,
        statutory_pay_date=run.statutory_pay_date,
        calculation_inputs=None,
        forecast_stale=True,
        signed_components=run.signed_components,
    )
    session.add(new)
    for c in run.components:
        new.components.append(
            PayrollComponent(
                kind=c.kind,
                label=c.label,
                forecast_pence=c.actual_pence if c.actual_pence is not None else c.forecast_pence,
            )
        )
    session.flush()
    return new


def _require_forecast(run: PayrollRun) -> None:
    if run.status is not PayrollRunStatus.forecast:
        raise PayrollError(
            f"payroll run {run.id} is {run.status} — reconciled runs "
            "are immutable; issue a correction instead"
        )


def runs_for_employment(session: Session, employment_id: int) -> list[PayrollRun]:
    return list(
        session.execute(
            select(PayrollRun)
            .where(PayrollRun.employment_id == employment_id)
            .order_by(PayrollRun.period_start, PayrollRun.id)
        )
        .scalars()
        .all()
    )


def apply_override(
    session: Session,
    run: PayrollRun,
    *,
    kind: PayrollComponentKind,
    delta_pence: int,
    reason: str,
    actor: str,
) -> PayrollOverride:
    """Append a run-scoped manual adjustment (issue #85).

    The override is a distinct component line (label carries the
    reason) plus an auditable ``payroll_override`` row. Deltas on
    earnings kinds move gross; every delta moves net by its own signed
    amount. Reconciled runs must be re-opened first; corrected runs
    are immutable forever.
    """
    if run.status is PayrollRunStatus.corrected:
        raise PayrollError(f"payroll run {run.id} is corrected — immutable")
    if run.status is PayrollRunStatus.reconciled:
        raise PayrollError(
            f"payroll run {run.id} is reconciled — re-open it before applying an override"
        )
    if delta_pence == 0:
        raise PayrollError("override delta must be non-zero")
    if not reason.strip():
        raise PayrollError("override requires a reason")
    ov = PayrollOverride(
        run_id=run.id, kind=kind, delta_pence=delta_pence, reason=reason.strip(), actor=actor
    )
    session.add(ov)
    run.components.append(
        PayrollComponent(
            kind=kind, label=f"Manual override — {reason.strip()}", forecast_pence=delta_pence
        )
    )
    if kind in EARNING:
        run.gross_pence += delta_pence
    run.net_pence += delta_pence
    snapshot = json.loads(run.calculation_inputs or "{}")
    if "taxable_pence" in snapshot:
        taxable_adjustment = kind in EARNING | {PayrollComponentKind.unpaid_leave}
        pension_adjustment = kind is PayrollComponentKind.pension and snapshot.get(
            "pension_mode"
        ) in {"net_pay", "salary_sacrifice"}
        if taxable_adjustment or pension_adjustment:
            snapshot["taxable_pence"] += delta_pence
        run.calculation_inputs = json.dumps(snapshot, sort_keys=True)
    else:
        run.forecast_stale = True
    mark_later_forecasts_stale(session, run)
    session.flush()
    return ov


def reopen(session: Session, run: PayrollRun) -> PayrollRun:
    """Compatibility action: preserve the original and open a linked correction."""
    return correct(session, run)


def sync_piecework_run(
    session: Session,
    employment: Employment,
    work_date: date,
) -> tuple[PayrollRun | None, PayrollRun | None]:
    """(Re)build the forecast run covering a piecework work date.

    Delivery entries feed runs at generation time; this keeps them in
    step when entries are logged/edited/deleted (issue #305). Returns
    (new_run, superseded_run) — the caller rematerializes both into
    the expected-income schedule. Mutable forecasts retain their ID,
    including an empty period after the last shift is removed; no zero
    income is projected. A reconciled covering run is never touched —
    it raises PayrollError so the caller surfaces a
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
        pay_date = work_date + timedelta(days=(rule.weekday - work_date.weekday()) % 7)
        start, end = pay_date - timedelta(days=6), pay_date
    else:
        start = work_date.replace(day=1)
        end = work_date.replace(day=calendar.monthrange(work_date.year, work_date.month)[1])
        pay_date = paydays.pay_date(rule, end.year, end.month) if rule is not None else end
    if employment.start_date is not None and start < employment.start_date:
        start = employment.start_date
    if employment.end_date is not None and end > employment.end_date:
        end = employment.end_date
    if end < start:
        return None, None

    has_work = session.scalar(
        select(DeliveryEntry.id)
        .where(
            DeliveryEntry.employment_id == employment.id,
            DeliveryEntry.work_date >= start,
            DeliveryEntry.work_date <= end,
            DeliveryEntry.source_transaction_id.is_(None),
        )
        .limit(1)
    )
    run = session.scalars(
        select(PayrollRun)
        .where(
            PayrollRun.employment_id == employment.id,
            PayrollRun.period_start == start,
            PayrollRun.period_end == end,
            PayrollRun.status != PayrollRunStatus.corrected,
        )
        .order_by(PayrollRun.id)
    ).first()
    if run is not None:
        if run.status is PayrollRunStatus.reconciled:
            raise PayrollError(
                f"{start}–{end} is already reconciled — issue a "
                "correction rather than changing logged work"
            )
        if (
            session.scalar(
                select(PayrollOverride.id).where(PayrollOverride.run_id == run.id).limit(1)
            )
            is not None
        ):
            raise PayrollError(
                f"run {run.id} has manual overrides — correct it "
                "manually so the adjustments are not lost"
            )
        preview = rebuild_forecasts(session, employment, apply=False, only_run_id=run.id)
        if not preview["changes"]:
            raise PayrollError(
                "payroll has actuals, bank matches or a pending correction; use correction"
            )
        rebuild_forecasts(
            session, employment, apply=True, fingerprint=preview["fingerprint"], only_run_id=run.id
        )
        mark_later_forecasts_stale(session, run)
        return run, None
    if has_work is None:
        return None, run
    new = generate(
        session, employment, start=start, end=end, pay_date=pay_date, allow_stale_rules=True
    )
    return new, run
