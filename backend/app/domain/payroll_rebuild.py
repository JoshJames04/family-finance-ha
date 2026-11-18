"""Reviewable forecast recalculation; never overwrite actuals or bank matches."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect, select, update
from sqlalchemy.orm import Session

from app.domain.payroll import PayrollError
from app.persistence.models import (
    DeliveryCharge,
    DeliveryEntry,
    Employment,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    OvertimeEntry,
    PayrollComponent,
    PayrollComponentKind,
    PayrollOverride,
    PayrollRun,
    PayrollRunStatus,
    SalaryRevision,
    TaxCodeRevision,
    TaxRuleSet,
)


def rebuild_forecasts(
    session: Session,
    employment: Employment,
    *,
    apply: bool,
    fingerprint: str | None = None,
    only_run_id: int | None = None,
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
    for model in (SalaryRevision, TaxCodeRevision, OvertimeEntry, DeliveryEntry):
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

    state.append([CALCULATION_VERSION, *embedded_rule_sets()])
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
    nested = session.begin_nested()
    try:
        for run in runs:
            if run.status is not PayrollRunStatus.forecast or (
                only_run_id is not None and run.id != only_run_id
            ):
                continue
            matched = session.scalar(
                select(ExpectedMatch.id)
                .join(ExpectedMovement, ExpectedMovement.id == ExpectedMatch.movement_id)
                .where(
                    ExpectedMovement.source_type == ExpectedSource.payroll_run,
                    ExpectedMovement.source_id == run.id,
                )
                .limit(1)
            )
            overridden = session.scalar(
                select(PayrollOverride.id).where(PayrollOverride.run_id == run.id).limit(1)
            )
            if (
                matched is not None
                or overridden is not None
                or run.corrects_id is not None
                or any(c.actual_pence is not None for c in run.components)
            ):
                warnings.append(
                    f"Run {run.id}: bank matches, actuals, correction or overrides preserved"
                )
                continue
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
                run.status = PayrollRunStatus.corrected
                session.flush()
                new = payrun.generate(
                    session,
                    employment,
                    start=run.period_start,
                    end=run.period_end,
                    pay_date=run.pay_date,
                    bonus_pence=adjustments[PayrollComponentKind.bonus],
                    allowance_pence=adjustments[PayrollComponentKind.allowance],
                    unpaid_leave_pence=-adjustments[PayrollComponentKind.unpaid_leave],
                    allow_stale_rules=True,
                    emit_event=False,
                    replacement_for=run.id,
                )
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
                        pay_date=run.pay_date.isoformat(),
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
