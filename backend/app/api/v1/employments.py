"""Employments: CRUD + effective-dated salary/tax-code revisions +
overtime entries."""

from __future__ import annotations

import calendar
import json
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import budgets as budget_dom
from app.domain import overtime as ot
from app.domain import pay_calendar, paydays, payrun, schedule
from app.domain import payroll as dom
from app.persistence.models import (
    Budget,
    DayType,
    Employment,
    OvertimeEntry,
    OvertimeStatus,
    PayrollRun,
    SalaryRevision,
    UserProfile,
)

router = APIRouter(prefix="/employments", tags=["employments"])


def _get(session: Session, household_id: int, emp_id: int) -> Employment:
    e = session.get(Employment, emp_id)
    if e is None or e.household_id != household_id:
        raise HTTPException(404, f"employment {emp_id} not found")
    return e


@router.get("", response_model=schemas.Page[schemas.EmploymentOut])
def list_employments(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> schemas.Page[schemas.EmploymentOut]:
    stmt = select(Employment).where(
        Employment.household_id == household.id)
    if profile is not None:
        stmt = stmt.where(Employment.profile_id == profile.id)
    total = len(list(session.execute(stmt).scalars().all()))
    rows = session.execute(
        stmt.order_by(Employment.id).offset((page - 1) * size)
        .limit(size)).scalars().all()
    return schemas.Page(
        items=[schemas.EmploymentOut.model_validate(e) for e in rows],
        total=total, page=page, size=size)


@router.post("", response_model=schemas.EmploymentOut, status_code=201)
def create_employment(
    body: schemas.EmploymentIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.EmploymentOut:
    p = session.get(UserProfile, body.profile_id)
    if p is None or p.household_id != household.id:
        raise HTTPException(404, f"profile {body.profile_id} not found")
    e = Employment(household_id=household.id,
                   profile_id=p.id, employer=body.employer,
                   jurisdiction=body.jurisdiction,
                   ni_category=body.ni_category,
                   student_loan_plan=body.student_loan_plan,
                   pension_scheme_ref=body.pension_scheme_ref,
                   contracted_hours=body.contracted_hours,
                   payday_rule=body.payday_rule,
                   pension_mode=body.pension_mode,
                   pension_employee_bps=body.pension_employee_bps,
                   pension_fixed_pence=body.pension_fixed_pence,
                   work_days_per_week=body.work_days_per_week,
                   overtime_multipliers=json.dumps(
                       {k.value: int(v) for k, v in
                        (body.overtime_multipliers or {}).items()}))
    dom.create_employment(session, e)
    emit(session, household.id, actor, "create", "employment", e.id,
         {"profile_id": p.id, "employer": e.employer})
    return schemas.EmploymentOut.model_validate(e)


@router.delete("/{emp_id}", status_code=204)
def delete_employment(
    emp_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> None:
    """Remove an employment that has no payroll runs or overtime —
    a wrong/fake entry. Once it carries history it can only be
    end-dated (PATCH end_date), never deleted (issue #304)."""
    e = _get(session, household.id, emp_id)
    has_runs = session.execute(
        select(PayrollRun.id).where(
            PayrollRun.employment_id == e.id).limit(1)).first()
    has_ot = session.execute(
        select(OvertimeEntry.id).where(
            OvertimeEntry.employment_id == e.id).limit(1)).first()
    if has_runs is not None or has_ot is not None:
        raise HTTPException(
            409, "employment has payroll runs or overtime — "
                 "set an end_date instead of deleting")
    for rev in list(e.salary_revisions):
        session.delete(rev)
    session.delete(e)
    emit(session, household.id, actor, "delete", "employment", emp_id,
         {"employer": e.employer})


def _shift_year(d: date, year: int) -> date:
    """Same month/day one year on, clamped for Feb 29."""
    try:
        return d.replace(year=year)
    except ValueError:
        return d.replace(year=year, day=28)


@router.post("/prepare-year", response_model=schemas.PrepareYearOut,
             status_code=201)
def prepare_year(
    body: schemas.PrepareYearIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.PrepareYearOut:
    """Roll the year forward (issue #304): forecast payroll runs for
    every employment active in the year + budget structure copied
    forward one year. Idempotent — existing runs/budgets skip."""
    year = body.year
    warnings: list[str] = []
    runs_created = runs_skipped = budgets_created = 0
    emps = session.scalars(
        select(Employment).where(
            Employment.household_id == household.id)).all()
    for e in emps:
        if e.end_date is not None and e.end_date < date(year, 1, 1):
            continue                      # ended before the year
        if not e.payday_rule:
            warnings.append(
                f"{e.employer}: no payday rule — runs skipped")
            continue
        rule = pay_calendar.parse_rule(e.payday_rule)
        for m in range(1, 13):
            start = date(year, m, 1)
            if e.end_date is not None and start > e.end_date:
                break                     # ended mid-year
            end = date(year, m, calendar.monthrange(year, m)[1])
            if e.end_date is not None and end > e.end_date:
                end = e.end_date
            exists = session.execute(
                select(PayrollRun.id).where(
                    PayrollRun.employment_id == e.id,
                    PayrollRun.period_start == start,
                    PayrollRun.period_end == end)
                .limit(1)).first()
            if exists is not None:
                runs_skipped += 1
                continue
            r = payrun.generate(
                session, e, start=start, end=end,
                pay_date=paydays.pay_date(rule, year, m),
                allow_stale_rules=True)
            schedule.rematerialize_payroll_run(session, r)
            runs_created += 1
    for b in session.scalars(
            select(Budget).where(
                Budget.household_id == household.id)).all():
        if b.end_date.year != year - 1:
            continue                      # only budgets ending last year
        new_start, new_end = _shift_year(b.start_date, year), _shift_year(
            b.end_date, year)
        clash = session.execute(
            select(Budget.id).where(
                Budget.household_id == household.id,
                Budget.owner_profile_id == b.owner_profile_id,
                Budget.start_date == new_start,
                Budget.end_date == new_end).limit(1)).first()
        if clash is not None:
            continue                      # already prepared
        _, w = budget_dom.copy_forward(session, b, b.name,
                                       new_start, new_end)
        warnings += w
        budgets_created += 1
    emit(session, household.id, actor, "prepare_year", "household",
         household.id, {"year": year, "runs_created": runs_created,
                        "budgets_created": budgets_created})
    return schemas.PrepareYearOut(
        year=year, runs_created=runs_created,
        runs_skipped=runs_skipped, budgets_created=budgets_created,
        warnings=warnings)


@router.get("/{emp_id}", response_model=schemas.EmploymentOut)
def get_employment(emp_id: int, household: HouseholdDep,
                   session: SessionDep) -> schemas.EmploymentOut:
    return schemas.EmploymentOut.model_validate(
        _get(session, household.id, emp_id))


@router.patch("/{emp_id}", response_model=schemas.EmploymentOut)
def patch_employment(
    emp_id: int, body: schemas.EmploymentPatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.EmploymentOut:
    e = _get(session, household.id, emp_id)
    changed = body.model_dump(exclude_unset=True)
    if "overtime_multipliers" in changed:
        changed["overtime_multipliers"] = json.dumps(
            {DayType(k).value: int(v) for k, v in
             (changed["overtime_multipliers"] or {}).items()})
    for k, v in changed.items():
        setattr(e, k, v)
    session.flush()
    emit(session, household.id, actor, "update", "employment", e.id,
         {k: str(v) for k, v in changed.items()})
    return schemas.EmploymentOut.model_validate(e)


@router.get("/{emp_id}/salary-revisions",
            response_model=list[schemas.SalaryRevisionOut])
def list_salary_revisions(
    emp_id: int, household: HouseholdDep, session: SessionDep,
) -> list[schemas.SalaryRevisionOut]:
    e = _get(session, household.id, emp_id)
    return [schemas.SalaryRevisionOut.model_validate(r)
            for r in e.salary_revisions]


@router.post("/{emp_id}/salary-revisions",
             response_model=schemas.SalaryRevisionOut, status_code=201)
def add_salary_revision(
    emp_id: int, body: schemas.SalaryRevisionIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.SalaryRevisionOut:
    e = _get(session, household.id, emp_id)
    rev = dom.add_salary_revision(session, e, body.effective_date,
                                  body.annual_salary.pence, body.notes)
    emit(session, household.id, actor, "create", "salary_revision",
         rev.id, {"employment_id": e.id,
                  "effective_date": str(rev.effective_date)})
    return schemas.SalaryRevisionOut.model_validate(rev)


def _get_revision(session: Session, household_id: int, emp_id: int,
                  rev_id: int) -> SalaryRevision:
    e = _get(session, household_id, emp_id)
    rev = session.get(SalaryRevision, rev_id)
    if rev is None or rev.employment_id != e.id:
        raise HTTPException(404, f"salary revision {rev_id} not found")
    return rev


@router.patch("/{emp_id}/salary-revisions/{rev_id}",
              response_model=schemas.SalaryRevisionOut)
def patch_salary_revision(
    emp_id: int, rev_id: int, body: schemas.SalaryRevisionPatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.SalaryRevisionOut:
    rev = _get_revision(session, household.id, emp_id, rev_id)
    changed = body.model_dump(exclude_unset=True)
    dom.update_salary_revision(
        session, rev,
        effective_date=changed.get("effective_date"),
        annual_pence=changed.get("annual_salary"),
        notes=changed.get("notes", ...))
    emit(session, household.id, actor, "update", "salary_revision",
         rev.id, {k: str(v) for k, v in changed.items()})
    return schemas.SalaryRevisionOut.model_validate(rev)


@router.delete("/{emp_id}/salary-revisions/{rev_id}",
               status_code=204)
def delete_salary_revision(
    emp_id: int, rev_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> None:
    rev = _get_revision(session, household.id, emp_id, rev_id)
    dom.delete_salary_revision(session, rev)
    emit(session, household.id, actor, "delete", "salary_revision",
         rev_id, {"employment_id": emp_id,
                  "effective_date": str(rev.effective_date)})


@router.get("/{emp_id}/tax-code-revisions",
            response_model=list[schemas.TaxCodeRevisionOut])
def list_tax_code_revisions(
    emp_id: int, household: HouseholdDep, session: SessionDep,
) -> list[schemas.TaxCodeRevisionOut]:
    e = _get(session, household.id, emp_id)
    return [schemas.TaxCodeRevisionOut.model_validate(r)
            for r in e.tax_code_revisions]


@router.post("/{emp_id}/tax-code-revisions",
             response_model=schemas.TaxCodeRevisionOut, status_code=201)
def add_tax_code_revision(
    emp_id: int, body: schemas.TaxCodeRevisionIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.TaxCodeRevisionOut:
    e = _get(session, household.id, emp_id)
    rev = dom.add_tax_code_revision(session, e, body.effective_date,
                                    body.tax_code, body.notes)
    emit(session, household.id, actor, "create", "tax_code_revision",
         rev.id, {"employment_id": e.id,
                  "effective_date": str(rev.effective_date)})
    return schemas.TaxCodeRevisionOut.model_validate(rev)


@router.get("/{emp_id}/salary",
            response_model=schemas.SalaryAtOut)
def salary_at(
    emp_id: int, on: Annotated[date, Query()],
    household: HouseholdDep, session: SessionDep,
) -> schemas.SalaryAtOut:
    e = _get(session, household.id, emp_id)
    annual = dom.salary_at(session, e.id, on)
    return schemas.SalaryAtOut(
        on=on, annual_salary_pence=annual,
        hourly_rate_pence=dom.hourly_rate_pence(
            annual, Decimal(str(e.contracted_hours))))


@router.get("/{emp_id}/gross",
            response_model=schemas.GrossPeriodOut)
def gross_for_period(
    emp_id: int,
    start: Annotated[date, Query()], end: Annotated[date, Query()],
    household: HouseholdDep, session: SessionDep,
) -> schemas.GrossPeriodOut:
    e = _get(session, household.id, emp_id)
    return schemas.GrossPeriodOut(
        start=start, end=end,
        gross_pence=dom.gross_for_period(session, e.id, start, end))


def _entry(session: Session, emp_id: int, entry_id: int
           ) -> OvertimeEntry:
    o = session.get(OvertimeEntry, entry_id)
    if o is None or o.employment_id != emp_id:
        raise HTTPException(404, f"overtime entry {entry_id} not found")
    return o


@router.get("/{emp_id}/overtime-entries",
            response_model=list[schemas.OvertimeOut])
def list_overtime(
    emp_id: int, household: HouseholdDep, session: SessionDep,
    status: OvertimeStatus | None = None,
) -> list[schemas.OvertimeOut]:
    e = _get(session, household.id, emp_id)
    stmt = select(OvertimeEntry).where(
        OvertimeEntry.employment_id == e.id)
    if status is not None:
        stmt = stmt.where(OvertimeEntry.status == status)
    rows = session.execute(
        stmt.order_by(OvertimeEntry.work_date)).scalars().all()
    return [schemas.OvertimeOut.model_validate(r) for r in rows]


@router.post("/{emp_id}/overtime-entries",
             response_model=schemas.OvertimeOut, status_code=201)
def create_overtime(
    emp_id: int, body: schemas.OvertimeIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.OvertimeOut:
    e = _get(session, household.id, emp_id)
    o = ot.create_entry(
        session, e, work_date=body.work_date, hours=body.hours,
        multiplier_bps=body.multiplier_bps, day_type=body.day_type,
        notes=body.notes)
    emit(session, household.id, actor, "create", "overtime_entry",
         o.id, {"employment_id": e.id, "work_date": str(o.work_date),
                "day_type": o.day_type.value, "hours": str(o.hours)})
    return schemas.OvertimeOut.model_validate(o)


@router.post("/{emp_id}/overtime-entries/{entry_id}/{action}",
             response_model=schemas.OvertimeOut)
def overtime_transition(
    emp_id: int, entry_id: int, action: str,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.OvertimeOut:
    _get(session, household.id, emp_id)
    o = _entry(session, emp_id, entry_id)
    handlers = {"submit": ot.submit, "approve": ot.approve,
                "reject": ot.reject, "rework": ot.rework}
    if action not in handlers:
        raise HTTPException(404, f"unknown action {action!r}")
    handlers[action](o)
    session.flush()
    emit(session, household.id, actor, action, "overtime_entry", o.id,
         {"employment_id": emp_id, "status": o.status.value})
    return schemas.OvertimeOut.model_validate(o)


@router.patch("/{emp_id}/overtime-entries/{entry_id}",
              response_model=schemas.OvertimeOut)
def patch_overtime(
    emp_id: int, entry_id: int, body: schemas.OvertimePatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.OvertimeOut:
    e = _get(session, household.id, emp_id)
    o = _entry(session, emp_id, entry_id)
    changed = body.model_dump(exclude_unset=True)
    ot.update_entry(
        session, o, e,
        work_date=changed.get("work_date"),
        hours=changed.get("hours"),
        multiplier_bps=changed.get("multiplier_bps"),
        day_type=changed.get("day_type"),
        notes=changed.get("notes", ...))
    emit(session, household.id, actor, "update", "overtime_entry", o.id,
         {"employment_id": emp_id,
          "fields": sorted(changed.keys())})
    return schemas.OvertimeOut.model_validate(o)


@router.delete("/{emp_id}/overtime-entries/{entry_id}", status_code=204)
def delete_overtime(
    emp_id: int, entry_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> None:
    _get(session, household.id, emp_id)
    o = _entry(session, emp_id, entry_id)
    ot.delete_entry(session, o)
    emit(session, household.id, actor, "delete", "overtime_entry",
         entry_id, {"employment_id": emp_id,
                    "work_date": str(o.work_date)})


def _run(session: Session, emp_id: int, run_id: int) -> PayrollRun:
    r = session.get(PayrollRun, run_id)
    if r is None or r.employment_id != emp_id:
        raise HTTPException(404, f"payroll run {run_id} not found")
    return r


@router.get("/{emp_id}/payroll-runs",
            response_model=list[schemas.PayrollRunOut])
def list_payroll_runs(
    emp_id: int, household: HouseholdDep, session: SessionDep,
) -> list[schemas.PayrollRunOut]:
    e = _get(session, household.id, emp_id)
    return [schemas.PayrollRunOut.model_validate(r)
            for r in payrun.runs_for_employment(session, e.id)]


@router.post("/{emp_id}/payroll-runs",
             response_model=schemas.PayrollRunOut, status_code=201)
def create_payroll_run(
    emp_id: int, body: schemas.PayrollRunIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.PayrollRunOut:
    e = _get(session, household.id, emp_id)
    r = payrun.generate(
        session, e, start=body.period_start, end=body.period_end,
        pay_date=body.pay_date,
        bonus_pence=body.bonus.pence if body.bonus else 0,
        allowance_pence=body.allowance.pence
        if body.allowance else 0,
        unpaid_leave_pence=body.unpaid_leave.pence
        if body.unpaid_leave else 0,
        unpaid_leave_days=body.unpaid_leave_days)
    schedule.rematerialize_payroll_run(session, r)
    emit(session, household.id, actor, "create", "payroll_run", r.id,
         {"employment_id": e.id, "period_end": str(r.period_end),
          "status": r.status.value})
    return schemas.PayrollRunOut.model_validate(r)


@router.get("/{emp_id}/payroll-runs/{run_id}",
            response_model=schemas.PayrollRunOut)
def get_payroll_run(
    emp_id: int, run_id: int, household: HouseholdDep,
    session: SessionDep,
) -> schemas.PayrollRunOut:
    _get(session, household.id, emp_id)
    return schemas.PayrollRunOut.model_validate(
        _run(session, emp_id, run_id))


@router.post("/{emp_id}/payroll-runs/{run_id}/reconcile",
             response_model=schemas.PayrollRunOut)
def reconcile_payroll_run(
    emp_id: int, run_id: int, body: schemas.PayrollReconcileIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.PayrollRunOut:
    _get(session, household.id, emp_id)
    r = _run(session, emp_id, run_id)
    variances = payrun.reconcile(session, r, body.actuals)
    schedule.rematerialize_payroll_run(session, r)  # → scheduled
    emit(session, household.id, actor, "reconcile", "payroll_run",
         r.id, {"employment_id": emp_id,
                "variance_kinds": [v.kind.value for v in variances],
                "variance_count": len(variances)})
    return schemas.PayrollRunOut.model_validate(r)


@router.post("/{emp_id}/payroll-runs/{run_id}/correct",
             response_model=schemas.PayrollRunOut, status_code=201)
def correct_payroll_run(
    emp_id: int, run_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.PayrollRunOut:
    _get(session, household.id, emp_id)
    r = _run(session, emp_id, run_id)
    new = payrun.correct(session, r)
    schedule.rematerialize_payroll_run(session, r)    # drop corrected
    schedule.rematerialize_payroll_run(session, new)  # add correction
    emit(session, household.id, actor, "correct", "payroll_run", r.id,
         {"employment_id": emp_id, "corrected_by": new.id})
    return schemas.PayrollRunOut.model_validate(new)


@router.post("/{emp_id}/payroll-runs/{run_id}/reopen",
             response_model=schemas.PayrollRunOut)
def reopen_payroll_run(
    emp_id: int, run_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.PayrollRunOut:
    """Re-open a reconciled run so a manual override can be applied
    (issue #85) — audited; actuals are preserved."""
    _get(session, household.id, emp_id)
    r = _run(session, emp_id, run_id)
    payrun.reopen(session, r)
    emit(session, household.id, actor, "reopen", "payroll_run", r.id,
         {"employment_id": emp_id, "status": r.status.value})
    return schemas.PayrollRunOut.model_validate(r)


@router.post("/{emp_id}/payroll-runs/{run_id}/overrides",
             response_model=schemas.PayrollOverrideOut,
             status_code=201)
def override_payroll_run(
    emp_id: int, run_id: int, body: schemas.PayrollOverrideIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.PayrollOverrideOut:
    """Run-scoped manual adjustment with mandatory reason (issue #85).
    Visible as a distinct component line; never carried forward."""
    _get(session, household.id, emp_id)
    r = _run(session, emp_id, run_id)
    ov = payrun.apply_override(
        session, r, kind=body.kind, delta_pence=body.delta.pence,
        reason=body.reason, actor=actor or "unknown")
    schedule.rematerialize_payroll_run(session, r)
    emit(session, household.id, actor, "override", "payroll_run", r.id,
         {"employment_id": emp_id, "kind": ov.kind.value,
          "reason": ov.reason})
    return schemas.PayrollOverrideOut.model_validate(ov)
