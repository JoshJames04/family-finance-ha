"""Reviewable forecast recalculation; never overwrite actuals or bank matches."""

from __future__ import annotations

import calendar
import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect, select, update
from sqlalchemy.orm import Session

from app.domain.payroll import PayrollError
from app.persistence.models import (
    DeliveryCharge,
    DeliveryEntry,
    Employment,
    EmploymentBonus,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    OvertimeEntry,
    PayrollComponent,
    PayrollComponentKind,
    PayrollOverride,
    PayrollRun,
    PayrollRunStatus,
    Payslip,
    SalaryRevision,
    TaxCodeRevision,
    TaxRuleSet,
)


def _forecast_calendar(employment: Employment, run: PayrollRun) -> tuple[date, date, date, bool]:
    """Preserve payroll month and one-off dates while updating regular paydays."""
    from app.domain import pay_calendar, paydays

    try:
        snapshot = json.loads(run.calculation_inputs or "{}")
        label = date.fromisoformat(
            snapshot.get("payroll_month", run.period_end.replace(day=1).isoformat())
        )
        end = date.fromisoformat(snapshot.get("automatic_period_end", run.period_end.isoformat()))
        if employment.payday_rule and "cutoff_day" in employment.payday_rule:
            end = label.replace(day=calendar.monthrange(label.year, label.month)[1])
        old_rule = snapshot.get("payday_rule", employment.payday_rule)
        if not old_rule or not employment.payday_rule:
            return label, run.pay_date, end, True
        old = pay_calendar.parse_rule(old_rule)
        new = pay_calendar.parse_rule(employment.payday_rule)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PayrollError(
            "configure a valid payday rule and forecast calendar before rebuilding"
        ) from exc
    periodic = {paydays.RuleKind.weekly, paydays.RuleKind.nightly}
    if old.kind in periodic or new.kind in periodic:
        if old.kind != new.kind or old.weekday != new.weekday:
            raise PayrollError(
                "pay frequency changed; review and create the new payroll periods explicitly"
            )
        if snapshot.get("cash_date_override") is True:
            return label, run.pay_date, end, True
        if old.kind is paydays.RuleKind.weekly:
            nominal = run.statutory_pay_date or run.period_end
            if paydays.adjust(nominal, old.adjust) == run.pay_date:
                return label, paydays.adjust(nominal, new.adjust), end, False
        return label, run.pay_date, end, True
    if (
        snapshot.get("cash_date_override") is True
        or paydays.pay_date(old, label.year, label.month) != run.pay_date
    ):
        return label, run.pay_date, end, True  # Deliberate one-off forecast date.
    return label, paydays.pay_date(new, label.year, label.month), end, False


def protected_run_ids(session: Session, runs: list[PayrollRun]) -> set[int]:
    """Rows whose independent evidence must survive any automatic refresh."""
    payslip_ids = set(
        session.scalars(
            select(Payslip.payroll_run_id).where(Payslip.payroll_run_id.in_([r.id for r in runs]))
        )
    )
    overridden_ids = set(
        session.scalars(
            select(PayrollOverride.run_id).where(PayrollOverride.run_id.in_([r.id for r in runs]))
        )
    )
    matched_ids = set(
        session.scalars(
            select(ExpectedMovement.source_id)
            .join(ExpectedMatch, ExpectedMatch.movement_id == ExpectedMovement.id)
            .where(
                ExpectedMovement.source_type == ExpectedSource.payroll_run,
                ExpectedMovement.source_id.in_([r.id for r in runs]),
            )
        )
    )
    return {
        r.id
        for r in runs
        if r.status is not PayrollRunStatus.forecast
        or r.id in overridden_ids
        or r.id in matched_ids
        or r.id in payslip_ids
        or r.corrects_id is not None
        or any(c.actual_pence is not None for c in r.components)
    }


def rebuild_forecasts(
    session: Session,
    employment: Employment,
    *,
    apply: bool,
    fingerprint: str | None = None,
    only_run_id: int | None = None,
    from_pay_date: date | None = None,
    preserve_incomplete_gross: bool = False,
) -> dict[str, Any]:
    from app.domain import payrun, schedule

    if apply:
        # Serialize with bank sync and other payroll writers before reading
        # inputs; a changed preview must not win a race with a new actual.
        session.execute(
            update(Employment)
            .where(Employment.id == employment.id)
            .values(id=Employment.id, updated_at=Employment.updated_at)
        )
        session.refresh(employment)
    runs = list(
        session.scalars(
            select(PayrollRun)
            .where(PayrollRun.employment_id == employment.id)
            .order_by(PayrollRun.pay_date, PayrollRun.id)
        )
    )
    runs.sort(key=lambda r: (payrun.effective_pay_date(employment, r), r.id))
    inputs: list[Any] = [employment, *runs]
    for model in (SalaryRevision, TaxCodeRevision, OvertimeEntry, DeliveryEntry, EmploymentBonus):
        inputs.extend(
            session.scalars(
                select(model).where(model.employment_id == employment.id).order_by(model.id)
            )
        )
    delivery_ids = list(
        session.scalars(
            select(DeliveryEntry.id).where(DeliveryEntry.employment_id == employment.id)
        )
    )
    inputs.extend(
        session.scalars(
            select(DeliveryCharge)
            .where(DeliveryCharge.delivery_entry_id.in_(delivery_ids))
            .order_by(DeliveryCharge.id)
        )
    )
    inputs.extend(
        session.scalars(
            select(PayrollOverride)
            .where(PayrollOverride.run_id.in_([r.id for r in runs]))
            .order_by(PayrollOverride.id)
        )
    )
    inputs.extend(session.scalars(select(TaxRuleSet).order_by(TaxRuleSet.id)))
    inputs.extend(
        session.scalars(
            select(Payslip)
            .where(Payslip.payroll_run_id.in_([r.id for r in runs]))
            .order_by(Payslip.id)
        )
    )
    movement_ids = list(
        session.scalars(
            select(ExpectedMovement.id).where(
                ExpectedMovement.source_type == ExpectedSource.payroll_run,
                ExpectedMovement.source_id.in_([r.id for r in runs]),
            )
        )
    )
    inputs.extend(
        session.scalars(
            select(ExpectedMatch)
            .where(ExpectedMatch.movement_id.in_(movement_ids))
            .order_by(ExpectedMatch.id)
        )
    )
    for run in runs:
        inputs.extend(sorted(run.components, key=lambda c: c.id))
    state = [[getattr(row, c.key) for c in inspect(type(row)).columns] for row in inputs]
    # Embedded rules may change between preview and an app upgrade.
    from app.domain.tax import CALCULATION_VERSION, embedded_rule_sets

    state.append([CALCULATION_VERSION, from_pay_date, *embedded_rule_sets()])
    current = hashlib.sha256(
        json.dumps(
            state,
            default=lambda v: str(v.normalize()) if isinstance(v, Decimal) else str(v),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if apply and fingerprint != current:
        raise PayrollError("payroll inputs changed; preview the changes again before applying")
    changes: list[dict[str, Any]] = []
    warnings: list[str] = []
    protected_ids = protected_run_ids(session, runs)
    def selected(run: PayrollRun) -> bool:
        return (only_run_id is None or run.id == only_run_id) and (
            from_pay_date is None or payrun.effective_pay_date(employment, run) >= from_pay_date
        )

    calendars = {
        r.id: _forecast_calendar(employment, r)
        for r in runs
        if r.id not in protected_ids and selected(r)
    }
    runs.sort(
        key=lambda r: (
            payrun.statutory_date_for(employment, calendars[r.id][1], calendars[r.id][0])
            if r.id in calendars
            else payrun.effective_pay_date(employment, r),
            r.id,
        )
    )
    nested = session.begin_nested()
    try:
        for run in runs:
            if run.status is not PayrollRunStatus.forecast or not selected(run):
                continue
            if run.id in protected_ids:
                warnings.append(
                    f"Run {run.id}: payslips, bank matches, actuals, "
                    "correction or overrides preserved"
                )
                continue
            _, cash_date, generation_end, cash_override = calendars[run.id]
            if run.signed_components is not True and any(
                c.kind is PayrollComponentKind.unpaid_leave and c.forecast_pence > 0
                for c in run.components
            ):
                raise PayrollError("review the sign of legacy unpaid leave before rebuilding")
            with session.begin_nested():
                adjustments = {
                    kind: sum(c.forecast_pence for c in run.components if c.kind is kind)
                    for kind in (
                        PayrollComponentKind.bonus,
                        PayrollComponentKind.allowance,
                        PayrollComponentKind.unpaid_leave,
                    )
                }
                source_inputs = json.loads(run.calculation_inputs or "{}")
                days = source_inputs.get("unpaid_leave_days")
                leave_days = Decimal(days) if days is not None else None
                fixed_leave = source_inputs.get(
                    "unpaid_leave_fixed_pence", -adjustments[PayrollComponentKind.unpaid_leave]
                )
                leave_basis_unknown = bool(source_inputs.get("unpaid_leave_basis_unknown")) or (
                    bool(adjustments[PayrollComponentKind.unpaid_leave])
                    and "unpaid_leave_fixed_pence" not in source_inputs
                )
                if leave_basis_unknown:
                    warnings.append(
                        f"Run {run.id}: original leave basis unknown; cash deduction preserved"
                    )
                run.status = PayrollRunStatus.corrected
                session.flush()
                new = payrun.generate(
                    session,
                    employment,
                    start=run.period_start,
                    end=generation_end,
                    pay_date=cash_date,
                    bonus_pence=adjustments[PayrollComponentKind.bonus] - run.planned_bonus_pence,
                    allowance_pence=adjustments[PayrollComponentKind.allowance],
                    unpaid_leave_pence=fixed_leave,
                    unpaid_leave_days=leave_days,
                    allow_stale_rules=True,
                    emit_event=False,
                    replacement_for=run.id,
                )
                if (
                    preserve_incomplete_gross
                    and new.gross_pence != run.gross_pence
                    and sum(c.forecast_pence for c in run.components if c.kind in payrun.EARNING)
                    != run.gross_pence
                ):
                    # A verified overtime change can explain a new gross, but
                    # it cannot explain a discrepancy in the original earnings.
                    overtime_kind = PayrollComponentKind.overtime
                    old_overtime = sum(
                        c.forecast_pence for c in run.components if c.kind is overtime_kind
                    )
                    new_overtime = sum(
                        c.forecast_pence for c in new.components if c.kind is overtime_kind
                    )
                    if new.gross_pence - new_overtime + old_overtime != run.gross_pence:
                        raise PayrollError(
                            f"Run {run.id}: incomplete earning components do not explain "
                            "the stored gross; review before rebuilding"
                        )
                snapshot = json.loads(new.calculation_inputs or "{}")
                if "automatic_period_end" in source_inputs:
                    snapshot["automatic_period_end"] = source_inputs["automatic_period_end"]
                snapshot["cash_date_override"] = cash_override
                snapshot["unpaid_leave_basis_unknown"] = leave_basis_unknown
                new.calculation_inputs = json.dumps(snapshot, sort_keys=True)
                if new.id in {r.id for r in runs}:
                    raise PayrollError(
                        "rebuild overlaps an existing payroll run; correct the period explicitly"
                    )
                if any(
                    r.id != run.id
                    and r.period_start == new.period_start
                    and r.period_end == new.period_end
                    for r in runs
                ):
                    raise PayrollError(
                        "rebuild overlaps historical payroll; correct the period explicitly"
                    )
                changes.append(
                    dict(
                        run_id=run.id,
                        pay_date=new.pay_date.isoformat(),
                        old_pay_date=run.pay_date.isoformat(),
                        new_pay_date=new.pay_date.isoformat(),
                        old_period_start=run.period_start.isoformat(),
                        old_period_end=run.period_end.isoformat(),
                        new_period_start=new.period_start.isoformat(),
                        new_period_end=new.period_end.isoformat(),
                        old_gross_pence=run.gross_pence,
                        new_gross_pence=new.gross_pence,
                        old_net_pence=run.net_pence,
                        new_net_pence=new.net_pence,
                    )
                )
                run.status = PayrollRunStatus.forecast
                for field in (
                    "planned_bonus_pence",
                    "noncash_bonus_pence",
                    "pay_date",
                    "period_start",
                    "period_end",
                    "gross_pence",
                    "net_pence",
                    "rule_set_year",
                    "rule_set_version",
                    "tax_year",
                    "statutory_pay_date",
                    "calculation_inputs",
                    "signed_components",
                ):
                    setattr(run, field, getattr(new, field))
                run.components.clear()
                session.flush()
                for c in new.components:
                    run.components.append(
                        PayrollComponent(
                            kind=c.kind, label=c.label, forecast_pence=c.forecast_pence
                        )
                    )
                run.forecast_stale = False
                session.delete(new)
                session.flush()
                if apply:
                    schedule.rematerialize_payroll_run(session, run)
        if apply:
            nested.commit()
        else:
            nested.rollback()
    except Exception:
        nested.rollback()
        raise
    return dict(fingerprint=current, changes=changes, warnings=warnings, applied=apply)
