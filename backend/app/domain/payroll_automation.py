"""Maintain future payroll automatically without manufacturing verified actuals."""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain import pay_calendar, paydays, payroll_rebuild, payrun, schedule
from app.domain.payroll import PayrollError
from app.persistence.models import Employment, PayrollRun, PayrollRunStatus


def _rebuild_stale(session: Session, employment: Employment, result: dict[str, Any]) -> bool:
    runs = list(
        session.scalars(select(PayrollRun).where(PayrollRun.employment_id == employment.id))
    )
    protected = payroll_rebuild.protected_run_ids(session, runs)
    stale = [r for r in runs if r.status is PayrollRunStatus.forecast and r.forecast_stale]
    if any(r.id in protected for r in stale):
        result["issues"].append(
            "Forecast with actuals, bank matches, overrides or a correction preserved"
        )
    if any(r.id not in protected for r in stale):
        try:
            with session.begin_nested():
                preview = payroll_rebuild.rebuild_forecasts(session, employment, apply=False)
                applied = payroll_rebuild.rebuild_forecasts(
                    session,
                    employment,
                    apply=True,
                    fingerprint=preview["fingerprint"],
                )
                result["updated"] += len(applied["changes"])
                result["issues"].extend(applied["warnings"])
        except (PayrollError, ValueError) as error:
            result["issues"].append(str(error))
            # Do not generate later payroll using stale cumulative inputs.
            return False
    return True


def refresh_all(session: Session, *, today: date | None = None) -> None:
    """Trusted startup/daily maintenance, without provider calls."""
    for employment in session.scalars(select(Employment).order_by(Employment.id)):
        refresh(session, employment, today=today)


def refresh(
    session: Session, employment: Employment, *, today: date | None = None
) -> dict[str, Any]:
    """Save source changes even when incomplete inputs prevent a safe forecast.

    Calculation writes use savepoints; independent evidence uses exactly the
    same protection checks as the explicit preview/apply workflow.
    """
    today = today or date.today()
    result: dict[str, Any] = {"generated": 0, "updated": 0, "issues": []}
    session.flush()
    session.execute(
        update(Employment)
        .where(Employment.id == employment.id)
        .values(
            id=Employment.id,
            updated_at=Employment.updated_at,
        )
    )
    payrun.flag_outdated_forecasts(session, employment)
    runs = list(
        session.scalars(
            select(PayrollRun).where(
                PayrollRun.employment_id == employment.id,
            )
        )
    )
    # Reuse only our own safely withdrawn forecasts. Genuine correction history
    # remains immutable, and evidence added since withdrawal prevents reopening.
    for run in runs:
        snapshot = json.loads(run.calculation_inputs or "{}")
        if (
            run.status is PayrollRunStatus.corrected
            and snapshot.get("withdrawn_reason")
            and run.pay_date >= today
            and employment.is_active
            and (employment.end_date is None or run.period_start <= employment.end_date)
        ):
            run.status = PayrollRunStatus.forecast
            if run.id in payroll_rebuild.protected_run_ids(session, [run]):
                run.status = PayrollRunStatus.corrected
                result["issues"].append(
                    f"Run {run.id}: withdrawn forecast now has independent evidence; preserved"
                )
            else:
                snapshot.pop("withdrawn_reason")
                run.calculation_inputs = json.dumps(snapshot, sort_keys=True)
                run.forecast_stale = True
    protected = payroll_rebuild.protected_run_ids(session, runs)
    for run in runs:
        if (
            run.status is not PayrollRunStatus.forecast
            or run.pay_date < today
            or (
                employment.is_active
                and (employment.end_date is None or run.period_start <= employment.end_date)
            )
        ):
            continue
        if run.id in protected:
            result["issues"].append(
                f"Run {run.id}: post-employment forecast has independent evidence; preserved"
            )
            continue
        snapshot = json.loads(run.calculation_inputs or "{}")
        snapshot["withdrawn_reason"] = "employment inactive or period after end date"
        run.calculation_inputs = json.dumps(snapshot, sort_keys=True)
        run.status = PayrollRunStatus.corrected
        run.forecast_stale = False
        schedule.rematerialize_payroll_run(session, run, today=today)
        result["updated"] += 1
    session.flush()
    if not _rebuild_stale(session, employment, result):
        schedule.withdraw_employment_estimates(session, employment, today)
        return result
    if not employment.is_active or employment.employment_type == "piecework":
        schedule.rematerialize_employment(session, employment, today=today)
        return result
    if not employment.payday_rule:
        result["issues"].append("Configure a payday before payroll can be calculated")
        return result
    try:
        rule = pay_calendar.parse_rule(employment.payday_rule)
        start_limit = employment.start_date
        if start_limit is None and employment.salary_revisions:
            start_limit = min(r.effective_date for r in employment.salary_revisions)
        periods: list[tuple[date, date, date]] = []
        if rule.kind is paydays.RuleKind.weekly:
            assert rule.weekday is not None
            nominal = today + timedelta(days=(rule.weekday - today.weekday()) % 7)
            while nominal <= date(today.year + 1, 12, 31):
                periods.append(
                    (nominal - timedelta(days=6), nominal, paydays.adjust(nominal, rule.adjust))
                )
                nominal += timedelta(days=7)
        elif rule.kind is not paydays.RuleKind.nightly:
            label = today.replace(day=1)
            while label.year <= today.year + 1:
                periods.append(
                    (
                        label,
                        label.replace(day=calendar.monthrange(label.year, label.month)[1]),
                        paydays.pay_date(rule, label.year, label.month),
                    )
                )
                label = (label.replace(day=28) + timedelta(days=4)).replace(day=1)
        active = [r for r in runs if r.status is not PayrollRunStatus.corrected]
        covered_months = {
            json.loads(r.calculation_inputs or "{}").get(
                "payroll_month", r.period_end.replace(day=1).isoformat()
            )
            for r in active
        }
        for start, end, cash_date in periods:
            if cash_date < today or (start_limit is not None and end < start_limit):
                continue
            if employment.end_date is not None and start > employment.end_date:
                continue
            if rule.kind is paydays.RuleKind.weekly:
                if any((r.statutory_pay_date or r.period_end) == end for r in active):
                    continue
            elif end.replace(day=1).isoformat() in covered_months:
                continue
            start = max(start, start_limit or start)
            try:
                with session.begin_nested():
                    row = payrun.generate(
                        session,
                        employment,
                        start=start,
                        end=end,
                        pay_date=cash_date,
                        allow_stale_rules=True,
                    )
                    snapshot = json.loads(row.calculation_inputs or "{}")
                    snapshot["automatic_period_end"] = end.isoformat()
                    row.calculation_inputs = json.dumps(snapshot, sort_keys=True)
                    schedule.rematerialize_payroll_run(session, row, today=today)
                result["generated"] += 1
            except (PayrollError, ValueError) as error:
                result["issues"].append(str(error))
                break  # Later cumulative forecasts must not bridge a missing period.
    except (ValueError, KeyError, TypeError) as error:
        result["issues"].append(f"Check payroll setup: {error}")
    if result["generated"]:
        # Inserting a missing earlier forecast invalidates later cumulative pay.
        _rebuild_stale(session, employment, result)
    if result["issues"]:
        schedule.withdraw_employment_estimates(session, employment, today)
    else:
        schedule.rematerialize_employment(session, employment, today=today)
    return result
