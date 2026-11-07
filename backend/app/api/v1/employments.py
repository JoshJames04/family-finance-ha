"""Employments: CRUD + effective-dated salary/tax-code revisions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import payroll as dom
from app.persistence.models import (
    Employment,
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
                   payday_rule=body.payday_rule)
    dom.create_employment(session, e)
    emit(session, household.id, actor, "create", "employment", e.id,
         {"profile_id": p.id, "employer": e.employer})
    return schemas.EmploymentOut.model_validate(e)


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
    for k, v in changed.items():
        setattr(e, k, v)
    session.flush()
    emit(session, household.id, actor, "update", "employment", e.id,
         changed)
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
