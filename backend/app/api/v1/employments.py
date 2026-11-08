"""Employments: CRUD + effective-dated salary/tax-code revisions +
overtime entries."""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
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
    Account,
    Budget,
    DayType,
    DeliveryCharge,
    DeliveryEntry,
    Employment,
    ImportRecord,
    OvertimeEntry,
    OvertimeStatus,
    PayrollRun,
    SalaryRevision,
    Transaction,
    TransactionSource,
    TransactionStatus,
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
    prior: Employment | None = None
    replacement_end: date | None = None
    if body.replaces_employment_id is not None:
        prior = _get(session, household.id, body.replaces_employment_id)
        if prior.profile_id != p.id:
            raise HTTPException(422, "replacement employment must share the member")
        if prior.end_date is not None:
            raise HTTPException(422, "replacement employment is already ended")
        if not body.replacement_confirmed:
            raise HTTPException(422, "replacement end-date confirmation is required")
        if body.start_date is None:
            raise HTTPException(422, "replacement requires a start date")
        replacement_end = body.replacement_end_date or (
            body.start_date - timedelta(days=1))
        if replacement_end >= body.start_date:
            raise HTTPException(422, "prior employment must end before the new start")
    if body.start_date and body.initial_salary_effective_date \
            and body.initial_salary_effective_date < body.start_date:
        raise HTTPException(422, "salary effective date cannot precede employment start")
    e = Employment(household_id=household.id,
                   profile_id=p.id, employer=body.employer,
                   employment_type=body.employment_type,
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
                   start_date=body.start_date,
                   deduction_mode=body.deduction_mode,
                   overtime_multipliers=json.dumps(
                       {k.value: int(v) for k, v in
                        (body.overtime_multipliers or {}).items()}))
    dom.create_employment(session, e)
    if body.initial_salary is not None:
        dom.add_salary_revision(
            session, e,
            body.initial_salary_effective_date or body.start_date or date.today(),
            body.initial_salary.pence)
    if body.initial_tax_code is not None:
        dom.add_tax_code_revision(
            session, e,
            body.initial_salary_effective_date or body.start_date or date.today(),
            body.initial_tax_code)
    if prior is not None and replacement_end is not None:
        prior.end_date = replacement_end
        session.flush()
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
    has_delivery = session.execute(
        select(DeliveryEntry.id).where(
            DeliveryEntry.employment_id == e.id).limit(1)).first()
    if has_runs is not None or has_ot is not None or has_delivery is not None:
        raise HTTPException(
            409, "employment has payroll runs, overtime or work history — "
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
        if not e.is_active:
            continue
        if e.end_date is not None and e.end_date < date(year, 1, 1):
            continue                      # ended before the year
        employment_start = e.start_date
        if employment_start is None and e.salary_revisions:
            employment_start = min(
                revision.effective_date for revision in e.salary_revisions)
        if not e.payday_rule:
            warnings.append(
                f"{e.employer}: no payday rule — runs skipped")
            continue
        try:
            rule = pay_calendar.parse_rule(e.payday_rule)
        except (ValueError, KeyError):
            # legacy shorthand (e.g. "last_friday")
            if e.payday_rule == "last_friday":
                rule = paydays.PaydayRule(
                    kind=paydays.RuleKind.nth_weekday, weekday=4, nth=-1,
                    adjust=paydays.ShiftPolicy.none)
            else:
                warnings.append(
                    f"{e.employer}: unrecognised payday rule — skipped")
                continue
        if rule.kind is paydays.RuleKind.weekly:
            # weekly pay → one run per ISO week in the year
            assert rule.weekday is not None
            first = date(year, 1, 1)
            first += timedelta(days=(rule.weekday - first.weekday()) % 7)
            pd = first
            while pd <= date(year, 12, 31):
                start = pd - timedelta(days=6)
                end = pd
                if employment_start is not None and start < employment_start:
                    start = employment_start
                if e.end_date is not None and end > e.end_date:
                    end = e.end_date
                if end < start:
                    pd += timedelta(days=7)
                    continue
                if e.employment_type == "piecework":
                    has_work = session.scalar(select(DeliveryEntry.id).where(
                        DeliveryEntry.employment_id == e.id,
                        DeliveryEntry.work_date >= start,
                        DeliveryEntry.work_date <= end,
                        DeliveryEntry.source_transaction_id.is_(None)).limit(1))
                    if has_work is None:
                        pd += timedelta(days=7)
                        continue
                exists = session.execute(
                    select(PayrollRun.id).where(
                        PayrollRun.employment_id == e.id,
                        PayrollRun.period_start == start,
                        PayrollRun.period_end == end,
                        PayrollRun.corrects_id.is_(None))
                    .limit(1)).first()
                if exists is not None:
                    runs_skipped += 1
                    pd += timedelta(days=7)
                    continue
                try:
                    r = payrun.generate(
                        session, e, start=start, end=end, pay_date=pd,
                        allow_stale_rules=True)
                except (dom.PayrollError, ValueError) as error:
                    warnings.append(f"{e.employer}: {error}")
                    pd += timedelta(days=7)
                    continue
                schedule.rematerialize_payroll_run(session, r)
                runs_created += 1
                pd += timedelta(days=7)
            continue
        if rule.kind is paydays.RuleKind.nightly:
            if e.deduction_mode == "paye":
                warnings.append(
                    f"{e.employer}: nightly PAYE requires weekly aggregation; "
                    "choose gross-only for cash-in-hand work")
                continue
            entries = session.scalars(
                select(DeliveryEntry).where(
                    DeliveryEntry.employment_id == e.id,
                    DeliveryEntry.work_date >= date(year, 1, 1),
                    DeliveryEntry.work_date <= date(year, 12, 31),
                    DeliveryEntry.source_transaction_id.is_(None))
                .order_by(DeliveryEntry.work_date)).all()
            for work_date in sorted({entry.work_date for entry in entries}):
                if employment_start is not None and work_date < employment_start:
                    continue
                if e.end_date is not None and work_date > e.end_date:
                    continue
                exists = session.execute(
                    select(PayrollRun.id).where(
                        PayrollRun.employment_id == e.id,
                        PayrollRun.period_start == work_date,
                        PayrollRun.period_end == work_date,
                        PayrollRun.corrects_id.is_(None)).limit(1)).first()
                if exists is not None:
                    runs_skipped += 1
                    continue
                try:
                    r = payrun.generate(
                        session, e, start=work_date, end=work_date,
                        pay_date=work_date, allow_stale_rules=True)
                except (dom.PayrollError, ValueError) as error:
                    warnings.append(f"{e.employer}: {error}")
                    continue
                schedule.rematerialize_payroll_run(session, r)
                runs_created += 1
            continue
        for m in range(1, 13):
            month_start = date(year, m, 1)
            end = date(year, m, calendar.monthrange(year, m)[1])
            if employment_start is not None and end < employment_start:
                continue
            start = max(month_start, employment_start or month_start)
            if e.end_date is not None and start > e.end_date:
                break                     # ended mid-year
            if e.end_date is not None and end > e.end_date:
                end = e.end_date
            if e.employment_type == "piecework":
                has_work = session.scalar(select(DeliveryEntry.id).where(
                    DeliveryEntry.employment_id == e.id,
                    DeliveryEntry.work_date >= start,
                    DeliveryEntry.work_date <= end,
                    DeliveryEntry.source_transaction_id.is_(None)).limit(1))
                if has_work is None:
                    continue
            exists = session.execute(
                    select(PayrollRun.id).where(
                        PayrollRun.employment_id == e.id,
                        PayrollRun.period_start == start,
                        PayrollRun.period_end == end,
                        PayrollRun.corrects_id.is_(None))
                .limit(1)).first()
            if exists is not None:
                runs_skipped += 1
                continue
            try:
                r = payrun.generate(
                    session, e, start=start, end=end,
                    pay_date=paydays.pay_date(rule, year, m),
                    allow_stale_rules=True)
            except (dom.PayrollError, ValueError) as error:
                warnings.append(f"{e.employer}: {error}")
                continue
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
    if "end_date" in changed and changed["end_date"] is not None \
            and e.start_date is not None \
            and changed["end_date"] < e.start_date:
        raise HTTPException(422, "employment end date cannot precede start date")
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


def _piecework(session: Session, household_id: int, emp_id: int
               ) -> Employment:
    e = _get(session, household_id, emp_id)
    if e.employment_type != "piecework":
        raise HTTPException(
            409, f"employment {emp_id} is not a piecework employment")
    return e


def _sync_delivery_run(session: Session, e: Employment,
                       work_date: date) -> None:
    """Keep the forecast payroll run covering work_date in step with
    logged piecework (issue #305 — deliveries feed payroll)."""
    try:
        new_run, old_run = payrun.sync_piecework_run(
            session, e, work_date)
    except dom.PayrollError as error:
        raise HTTPException(409, str(error)) from error
    if old_run is not None:
        schedule.rematerialize_payroll_run(session, old_run)
    if new_run is not None:
        schedule.rematerialize_payroll_run(session, new_run)


@router.get("/{emp_id}/deliveries",
            response_model=list[schemas.DeliveryOut])
def list_deliveries(
    emp_id: int, household: HouseholdDep, session: SessionDep,
) -> list[schemas.DeliveryOut]:
    e = _piecework(session, household.id, emp_id)
    rows = session.execute(
        select(DeliveryEntry).where(
            DeliveryEntry.employment_id == e.id)
        .order_by(DeliveryEntry.work_date)).scalars().all()
    return [schemas.DeliveryOut.model_validate(r) for r in rows]


@router.post("/{emp_id}/deliveries",
             response_model=schemas.DeliveryOut, status_code=201)
def create_delivery(
    emp_id: int, body: schemas.DeliveryIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.DeliveryOut:
    e = _piecework(session, household.id, emp_id)
    if e.end_date is not None and body.work_date > e.end_date:
        raise HTTPException(
            422, f"work date is after employment end date {e.end_date}")
    if body.hours == 0 and body.deliveries == 0 and not body.charges:
        raise HTTPException(422, "a shift needs hours, deliveries or both")
    d = DeliveryEntry(
        employment_id=e.id, work_date=body.work_date, hours=body.hours,
        hourly_rate_pence=(body.hourly_rate.pence
                           if body.hourly_rate else 0),
        deliveries=body.deliveries,
        delivery_rate_pence=(body.delivery_rate.pence
                             if body.delivery_rate else 0),
        tips_pence=body.tips.pence if body.tips else 0,
        notes=body.notes)
    session.add(d)
    session.flush()
    d.charges = [
        DeliveryCharge(amount_pence=charge.amount.pence, label=charge.label)
        for charge in body.charges
    ]
    session.flush()
    _sync_delivery_run(session, e, d.work_date)
    emit(session, household.id, actor, "create", "delivery_entry", d.id,
         {"employment_id": e.id, "work_date": str(d.work_date),
          "pay_pence": d.pay_pence})
    return schemas.DeliveryOut.model_validate(d)


@router.patch("/{emp_id}/deliveries/{entry_id}",
              response_model=schemas.DeliveryOut)
def patch_delivery(
    emp_id: int, entry_id: int, body: schemas.DeliveryIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.DeliveryOut:
    _piecework(session, household.id, emp_id)
    d = session.get(DeliveryEntry, entry_id)
    if d is None or d.employment_id != emp_id:
        raise HTTPException(404, f"delivery entry {entry_id} not found")
    changed = body.model_dump(exclude_unset=True)
    employment = _get(session, household.id, emp_id)
    if employment.end_date is not None \
            and body.work_date > employment.end_date:
        raise HTTPException(
            422, "work date is after the employment end date")
    if "hourly_rate" in changed:
        changed.pop("hourly_rate")
        changed["hourly_rate_pence"] = (
            body.hourly_rate.pence if body.hourly_rate is not None else 0)
    if "delivery_rate" in changed:
        changed.pop("delivery_rate")
        changed["delivery_rate_pence"] = (
            body.delivery_rate.pence if body.delivery_rate is not None else 0)
    changed.pop("tips", None)
    if "charges" in changed:
        changed.pop("charges")
        d.charges.clear()
        d.charges.extend(DeliveryCharge(
            amount_pence=charge.amount.pence, label=charge.label)
            for charge in body.charges)
    if "tips" in body.model_fields_set:
        changed["tips_pence"] = body.tips.pence if body.tips else 0
    if changed.get("hours", d.hours) == 0 \
            and changed.get("deliveries", d.deliveries) == 0 \
            and not d.charges:
        raise HTTPException(422, "a shift needs hours, deliveries or charges")
    old_work_date = d.work_date
    for k, v in changed.items():
        setattr(d, k, v)
    session.flush()
    if old_work_date != d.work_date:
        _sync_delivery_run(session, employment, old_work_date)
    _sync_delivery_run(session, employment, d.work_date)
    emit(session, household.id, actor, "update", "delivery_entry", d.id,
         {"employment_id": emp_id, "fields": sorted(changed)})
    return schemas.DeliveryOut.model_validate(d)


@router.delete("/{emp_id}/deliveries/{entry_id}", status_code=204)
def delete_delivery(
    emp_id: int, entry_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> None:
    _piecework(session, household.id, emp_id)
    d = session.get(DeliveryEntry, entry_id)
    if d is None or d.employment_id != emp_id:
        raise HTTPException(404, f"delivery entry {entry_id} not found")
    if d.source_transaction_id is not None:
        raise HTTPException(
            409, "imported history is immutable; correct the source breakdown")
    employment = _get(session, household.id, emp_id)
    session.delete(d)
    session.flush()
    _sync_delivery_run(session, employment, d.work_date)
    emit(session, household.id, actor, "delete", "delivery_entry",
         entry_id, {"employment_id": emp_id,
                    "work_date": str(d.work_date)})


@router.get("/{emp_id}/piecework-import/preview",
            response_model=list[schemas.HistoricalPieceworkImportOut])
def preview_piecework_import(
    emp_id: int, household: HouseholdDep, session: SessionDep,
) -> list[schemas.HistoricalPieceworkImportOut]:
    """Preview imported income without changing source transactions."""
    _piecework(session, household.id, emp_id)
    rows = session.scalars(
        select(Transaction).join(Account).join(
            ImportRecord, ImportRecord.id == Transaction.import_record_id).where(
            Account.household_id == household.id,
            Transaction.source == TransactionSource.excel_import,
            Transaction.status == TransactionStatus.cleared,
            Transaction.amount_pence > 0,
            ImportRecord.sheet.ilike("%slow%boat%"))
        .order_by(Transaction.date, Transaction.id)).all()
    linked = set(session.scalars(select(
        DeliveryEntry.source_transaction_id).where(
            DeliveryEntry.employment_id == emp_id,
            DeliveryEntry.source_transaction_id.is_not(None))).all())
    return [
        schemas.HistoricalPieceworkImportOut(
            transaction_id=t.id, work_date=t.date,
            imported_total_pence=t.amount_pence,
            mapped_delivery_id=None if t.id not in linked else next(
                (d.id for d in session.scalars(select(DeliveryEntry).where(
                    DeliveryEntry.source_transaction_id == t.id))), None))
        for t in rows]


@router.post("/{emp_id}/piecework-import/confirm",
             response_model=list[schemas.DeliveryOut], status_code=201)
def confirm_piecework_import(
    emp_id: int, body: schemas.HistoricalPieceworkImportConfirmIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> list[schemas.DeliveryOut]:
    """Map imported amounts to immutable, review-flagged piecework shifts."""
    e = _piecework(session, household.id, emp_id)
    rows = session.scalars(
        select(Transaction).join(Account).join(
            ImportRecord, ImportRecord.id == Transaction.import_record_id).where(
            Account.household_id == household.id,
            Transaction.id.in_(body.transaction_ids),
            Transaction.source == TransactionSource.excel_import,
            Transaction.status == TransactionStatus.cleared,
            Transaction.amount_pence > 0,
            ImportRecord.sheet.ilike("%slow%boat%"))).all()
    found = {row.id: row for row in rows}
    if len(found) != len(set(body.transaction_ids)):
        raise HTTPException(404, "one or more source transactions not found")
    output: list[DeliveryEntry] = []
    for transaction in rows:
        existing = session.scalar(select(DeliveryEntry).where(
            DeliveryEntry.source_transaction_id == transaction.id))
        if existing is not None:
            if existing.employment_id != e.id:
                raise HTTPException(409, "source transaction already mapped")
            output.append(existing)
            continue
        shift = DeliveryEntry(
            employment_id=e.id, work_date=transaction.date, hours=0,
            hourly_rate_pence=0, deliveries=0, delivery_rate_pence=0,
            tips_pence=0, source_transaction_id=transaction.id,
            imported_total_pence=transaction.amount_pence,
            needs_review=True, notes="Imported historical amount; review split")
        shift.charges = [DeliveryCharge(
            amount_pence=transaction.amount_pence, label="Imported total")]
        session.add(shift)
        session.flush()
        output.append(shift)
        emit(session, household.id, actor, "create", "delivery_entry",
             shift.id, {"employment_id": e.id,
                        "source_transaction_id": transaction.id,
                        "needs_review": True})
    return [schemas.DeliveryOut.model_validate(row) for row in output]


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
