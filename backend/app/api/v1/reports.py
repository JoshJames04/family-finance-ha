"""Reports: deterministic monthly / annual / year-on-year documents."""

import calendar
from datetime import date
from typing import Annotated, TypedDict

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.deps import HouseholdDep, ProfileDep, SessionDep
from app.domain import rbac, reports
from app.persistence.models import Account, Budget, UserProfile

router = APIRouter(prefix="/reports", tags=["reports"])


def _month(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _year(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


@router.get("/monthly", response_model=schemas.ReportOut)
def monthly_report(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    year: Annotated[int, Query(ge=2000, le=2100)],
    month: Annotated[int, Query(ge=1, le=12)],
) -> schemas.ReportOut:
    start, end = _month(year, month)
    doc = reports.build_report(
        session, household.id, start, end, **_scope(session, household.id, profile))
    return schemas.ReportOut.model_validate(doc)


@router.get("/annual", response_model=schemas.ReportOut)
def annual_report(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    year: Annotated[int, Query(ge=2000, le=2100)],
) -> schemas.ReportOut:
    start, end = _year(year)
    doc = reports.build_report(
        session, household.id, start, end, **_scope(session, household.id, profile))
    return schemas.ReportOut.model_validate(doc)


@router.get("/yoy", response_model=schemas.ReportOut)
def year_on_year_report(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    year: Annotated[int, Query(ge=2001, le=2100)],
) -> schemas.ReportOut:
    start, end = _year(year)
    prior = _year(year - 1)
    doc = reports.build_report(
        session, household.id, start, end, prior=prior,
        **_scope(session, household.id, profile))
    return schemas.ReportOut.model_validate(doc)


class _ReportScope(TypedDict):
    account_ids: list[int]
    budget_ids: list[int]


def _scope(session: Session, household_id: int,
           profile: UserProfile | None) -> _ReportScope:
    return {"account_ids": [a.id for a in session.scalars(select(Account).where(
                Account.household_id == household_id))
                if rbac.visible(a.owner_profile_id, profile)],
            "budget_ids": [b.id for b in session.scalars(select(Budget).where(
                Budget.household_id == household_id))
                if rbac.visible(b.owner_profile_id, profile)]}
