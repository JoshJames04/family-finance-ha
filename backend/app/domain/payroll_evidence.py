"""Attach missing verified bases to immutable legacy actuals, never recalculate them."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.api.audit import emit
from app.domain import payroll_automation, payroll_totals, payrun
from app.domain.payroll import PayrollError
from app.persistence.models import Employment, PayrollComponentKind, PayrollRun, PayrollRunStatus


def verify_inputs(
    session: Session,
    employment: Employment,
    run_id: int,
    *,
    actor: str,
    source_sha256: str,
    payslip_gross_pence: int,
    payslip_net_pence: int,
    taxable_pence: int | None = None,
    niable_pence: int | None = None,
    ytd_taxable_pence: int | None = None,
    ytd_tax_pence: int | None = None,
) -> tuple[PayrollRun, dict[str, Any]]:
    """Trusted operator entry point; HTTP callers must separately enforce ownership.

    Existing facts may only be repeated. Corrections use the correction workflow.
    The source digest identifies an independently checked private payslip without
    storing its contents, filename or financial payload in the audit trail.
    """
    if re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256) is None:
        raise PayrollError("source_sha256 must be a SHA-256 digest")
    if (ytd_taxable_pence is None) != (ytd_tax_pence is None):
        raise PayrollError("enter both payslip YTD taxable pay and YTD PAYE, or neither")
    if any(v is not None and v < 0 for v in (
        payslip_gross_pence, taxable_pence, niable_pence, ytd_taxable_pence
    )):
        raise PayrollError("gross, taxable and NI earnings cannot be negative")
    # Acquire the same writer lock as forecast rebuilds before reading evidence.
    session.execute(update(Employment).where(Employment.id == employment.id).values(
        id=Employment.id, updated_at=Employment.updated_at
    ))
    session.refresh(employment)
    run = session.scalar(select(PayrollRun).where(
        PayrollRun.id == run_id, PayrollRun.employment_id == employment.id
    ).options(selectinload(PayrollRun.components)).execution_options(populate_existing=True))
    if run is None or run.status is not PayrollRunStatus.reconciled:
        raise PayrollError("only an existing reconciled payroll run can receive verified inputs")
    session.expire(run, ["employment"])
    if employment.deduction_mode != "gross_only" and taxable_pence is None:
        raise PayrollError("enter verified taxable pay from the payslip")
    if not run.components or any(c.actual_pence is None for c in run.components):
        raise PayrollError("every recorded component must have an explicit actual")
    if run.signed_components is False:
        raise PayrollError("unsigned actual components require the correction workflow")
    actuals: dict[PayrollComponentKind, int] = {}
    for component in run.components:
        assert component.actual_pence is not None
        actuals[component.kind] = actuals.get(component.kind, 0) + component.actual_pence
    gross = sum(v for k, v in actuals.items() if k in payroll_totals.EARNINGS)
    gross_after_leave = gross + actuals.get(PayrollComponentKind.unpaid_leave, 0)
    refunded_pension = max(0, actuals.get(PayrollComponentKind.pension, 0))
    # Legacy payslips may present leave as a post-gross deduction. Accept
    # either presentation only when independent gross ALSO matches the
    # recorded actual total below; taxable pay remains a separate fact.
    gross_presentations = {gross, gross_after_leave}
    gross_presentations.update(g + refunded_pension for g in list(gross_presentations))
    if run.signed_components is None and any(
        c.actual_pence is not None and c.actual_pence > 0
        and c.kind not in payroll_totals.EARNINGS
        and not (c.kind is PayrollComponentKind.pension
                 and payslip_gross_pence in {
                     gross + refunded_pension, gross_after_leave + refunded_pension
                 })
        for c in run.components
    ):
        raise PayrollError("ambiguous positive deductions require the correction workflow")
    if (sum(actuals.values()) != payslip_net_pence
            or payslip_gross_pence not in gross_presentations):
        raise PayrollError("recorded actual components must balance to payslip gross and net")
    recorded = payroll_totals.totals_for_run(run)
    if (recorded.actual_gross_pence != payslip_gross_pence
            or recorded.actual_net_pence != payslip_net_pence):
        raise PayrollError("payslip totals conflict with recorded actual totals; use correction")
    facts: dict[str, int | bool | None] = {
        "signed_components": True,
        "verified_gross_pence": payslip_gross_pence,
        "verified_net_pence": payslip_net_pence,
        "verified_taxable_pence": taxable_pence,
        "verified_niable_pence": niable_pence,
        "verified_ytd_taxable_pence": ytd_taxable_pence,
        "verified_ytd_tax_pence": ytd_tax_pence,
    }
    changed = []
    for field, value in facts.items():
        current = getattr(run, field)
        if value is None:
            continue
        if current is not None and current != value:
            raise PayrollError("verified evidence conflicts with existing facts; use correction")
        if current is None:
            changed.append(field)
    # Validate every field before writing anything or emitting an audit event.
    for field in changed:
        setattr(run, field, facts[field])
    if changed:
        payrun.mark_later_forecasts_stale(session, run)
        emit(session, employment.household_id, actor, "verify_inputs", "payroll_run", run.id,
             {"employment_id": employment.id, "owner_profile_id": employment.profile_id,
              "source_sha256": source_sha256.lower(),
              "fields": sorted(changed)})
        session.flush()
    result = payroll_automation.refresh(session, employment)
    return run, result
