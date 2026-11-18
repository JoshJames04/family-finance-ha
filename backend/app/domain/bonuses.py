"""Multiple gross bonus entries feed one employer/month payroll run."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import pay_calendar, paydays
from app.domain.payroll import PayrollError
from app.persistence.models import (
    Employment,
    EmploymentBonus,
    PayrollComponentKind,
    PayrollOverride,
    PayrollRun,
    PayrollRunStatus,
    SalaryRevision,
)


def _runs(session: Session, employment: Employment, month: date) -> list[PayrollRun]:
    return list(
        session.scalars(
            select(PayrollRun)
            .where(
                PayrollRun.employment_id == employment.id,
                PayrollRun.period_end >= month,
                PayrollRun.status != PayrollRunStatus.corrected,
            )
            .order_by(PayrollRun.pay_date, PayrollRun.id)
        )
    )


def _editable(session: Session, employment: Employment, month: date) -> None:
    from app.domain.payrun import EARNING

    for run in _runs(session, employment, month):
        if sum(c.forecast_pence for c in run.components if c.kind in EARNING) != run.gross_pence:
            raise PayrollError(
                "payroll has incomplete earning components; correct it before changing bonuses"
            )
        if run.status != PayrollRunStatus.forecast or any(
            c.actual_pence is not None for c in run.components
        ):
            raise PayrollError(
                "this or a later payroll is reconciled; use the payroll correction workflow"
            )
        if session.scalar(
            select(PayrollOverride.id).where(PayrollOverride.run_id == run.id).limit(1)
        ):
            raise PayrollError("payroll has manual overrides; use the payroll correction workflow")
        if len([c for c in run.components if c.kind == PayrollComponentKind.bonus]) > 1:
            raise PayrollError(
                "payroll has multiple imported bonus components; correct it manually"
            )


def _payment(employment: Employment, month: date) -> tuple[date, date, date]:
    if not employment.payday_rule:
        raise PayrollError("configure the employer payday before adding a monthly bonus")
    try:
        rule = pay_calendar.parse_rule(employment.payday_rule)
    except (ValueError, KeyError, TypeError):
        if employment.payday_rule in {"last_day", "last_working_day"}:
            rule = paydays.PaydayRule(kind=paydays.RuleKind(employment.payday_rule))
        elif employment.payday_rule == "last_friday":
            rule = paydays.PaydayRule(kind=paydays.RuleKind.nth_weekday, weekday=4, nth=-1)
        else:
            raise PayrollError("configure a supported monthly payday") from None
    if rule.kind not in {
        paydays.RuleKind.fixed_day,
        paydays.RuleKind.nth_weekday,
        paydays.RuleKind.last_day,
        paydays.RuleKind.last_working_day,
    }:
        raise PayrollError(
            "monthly bonuses require a monthly payday; use a dated payroll run for weekly pay"
        )
    start, end = month, month.replace(day=calendar.monthrange(month.year, month.month)[1])
    if "cutoff_day" in employment.payday_rule:
        window = pay_calendar.period(
            pay_calendar.parse_calendar(employment.payday_rule), month.year, month.month
        )
        start, end = window.start, window.cutoff
    if employment.start_date:
        start = max(start, employment.start_date)
    if employment.end_date and end > employment.end_date:
        end = employment.end_date
    pay = paydays.pay_date(rule, month.year, month.month)
    if (
        end < start
        or (employment.start_date and pay < employment.start_date)
        or (employment.end_date and pay > employment.end_date)
    ):
        raise PayrollError("bonus month is outside the employment dates")
    return start, end, pay


def _pence(value: Decimal) -> int:
    if not value.is_finite() or value < 0 or value > 999_999_999_999:
        raise PayrollError("bonus amount is outside the supported range")
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def save(
    session: Session,
    employment: Employment,
    *,
    pay_month: date,
    label: str = "Bonus",
    salary_percentage: Decimal | None = None,
    share_value_pence: int | None = None,
    manual_pence: int = 0,
    existing: EmploymentBonus | None = None,
) -> EmploymentBonus:
    if pay_month.day != 1 or not 1900 <= pay_month.year <= 2200:
        raise PayrollError("payment month must use its first day in 1900–2200")
    if existing is not None and existing.pay_month != pay_month:
        raise PayrollError("delete and recreate a bonus to change its payment month")
    if salary_percentage is not None and (
        not salary_percentage.is_finite() or salary_percentage < 0 or salary_percentage > 10000
    ):
        raise PayrollError("salary percentage is invalid")
    if manual_pence < 0 or (share_value_pence is not None and share_value_pence < 0):
        raise PayrollError("bonus inputs cannot be negative")
    if not label.strip() or len(label) > 200:
        raise PayrollError("bonus label must contain 1 to 200 characters")
    _editable(session, employment, pay_month)
    _, _, pay = _payment(employment, pay_month)
    covering = [
        r for r in _runs(session, employment, pay_month) if r.period_end.replace(day=1) == pay_month
    ]
    if len(covering) > 1 or any(r.pay_date != pay for r in covering):
        raise PayrollError(
            "existing payroll does not match this monthly payday; correct its period/date first"
        )
    basis = session.scalar(
        select(SalaryRevision)
        .where(SalaryRevision.employment_id == employment.id, SalaryRevision.effective_date <= pay)
        .order_by(SalaryRevision.effective_date.desc())
        .limit(1)
    )
    if (salary_percentage or share_value_pence) and basis is None:
        raise PayrollError("add salary/share history effective on the payment date")
    if share_value_pence and employment.share_payment_method == "outside_payroll":
        raise PayrollError(
            "this employer handles shares outside payroll; record share proceeds separately"
        )
    salary = basis.annual_salary_pence if basis else 0
    shares = basis.shares_owned if basis else Decimal("0")
    salary_part = _pence(Decimal(salary) * (salary_percentage or 0) / 100)
    share_part = _pence(shares * (share_value_pence or 0))
    gross = _pence(Decimal(salary_part + share_part + manual_pence))
    if gross == 0:
        raise PayrollError("enter at least one positive bonus amount")
    row = existing or EmploymentBonus(employment_id=employment.id)
    row.pay_month, row.pay_date, row.label = pay_month, pay, label.strip()
    row.salary_percentage, row.share_value_pence, row.manual_pence = (
        salary_percentage,
        share_value_pence,
        manual_pence,
    )
    row.salary_basis_pence, row.shares_owned = salary, shares
    row.salary_bonus_pence, row.share_bonus_pence, row.gross_pence = salary_part, share_part, gross
    row.share_payment_method = employment.share_payment_method
    row.noncash_pence = share_part if employment.share_payment_method == "payroll_noncash" else 0
    session.add(row)
    session.flush()
    refresh(session, employment, pay_month)
    return row


def remove(session: Session, employment: Employment, row: EmploymentBonus) -> None:
    _editable(session, employment, row.pay_month)
    month = row.pay_month
    session.delete(row)
    session.flush()
    refresh(session, employment, month)


def refresh(session: Session, employment: Employment, month: date) -> None:
    """Bonus planning uses the same guarded payroll rebuild as automatic forecasts."""
    from app.domain import payrun, schedule

    start, end, pay = _payment(employment, month)
    active = _runs(session, employment, month)
    covering = [
        r for r in active if r.period_end.year == month.year and r.period_end.month == month.month
    ]
    if len(covering) > 1:
        raise PayrollError(
            "multiple payroll runs cover this month; correct the payroll period first"
        )
    if not covering:
        payrun.generate(
            session, employment, start=start, end=end, pay_date=pay, allow_stale_rules=True
        )
    for run in sorted(
        _runs(session, employment, month),
        key=lambda r: (payrun.effective_pay_date(employment, r), r.id),
    ):
        preview = payrun.rebuild_forecasts(session, employment, apply=False, only_run_id=run.id)
        if not preview["changes"]:
            raise PayrollError(
                "payroll has protected history or manual adjustments; use a correction"
            )
        payrun.rebuild_forecasts(
            session, employment, apply=True, fingerprint=preview["fingerprint"], only_run_id=run.id
        )
    schedule.rematerialize_employment(session, employment)
