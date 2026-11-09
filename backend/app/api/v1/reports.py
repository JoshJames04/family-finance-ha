"""Reports: deterministic monthly / annual / year-on-year documents."""

import calendar
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api import schemas
from app.api.deps import HouseholdDep, SessionDep
from app.domain import reports

router = APIRouter(prefix="/reports", tags=["reports"])


def _month(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _year(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


@router.get("/monthly", response_model=schemas.ReportOut)
def monthly_report(
    household: HouseholdDep, session: SessionDep,
    year: Annotated[int, Query(ge=2000, le=2100)],
    month: Annotated[int, Query(ge=1, le=12)],
) -> schemas.ReportOut:
    start, end = _month(year, month)
    doc = reports.build_report(session, household.id, start, end)
    return schemas.ReportOut.model_validate(doc)


@router.get("/annual", response_model=schemas.ReportOut)
def annual_report(
    household: HouseholdDep, session: SessionDep,
    year: Annotated[int, Query(ge=2000, le=2100)],
) -> schemas.ReportOut:
    start, end = _year(year)
    doc = reports.build_report(session, household.id, start, end)
    return schemas.ReportOut.model_validate(doc)


@router.get("/yoy", response_model=schemas.ReportOut)
def year_on_year_report(
    household: HouseholdDep, session: SessionDep,
    year: Annotated[int, Query(ge=2001, le=2100)],
) -> schemas.ReportOut:
    start, end = _year(year)
    prior = _year(year - 1)
    doc = reports.build_report(
        session, household.id, start, end, prior=prior)
    return schemas.ReportOut.model_validate(doc)
