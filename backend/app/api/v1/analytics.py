"""Analytics: materialised monthly aggregates + time-series reads."""

from typing import Annotated, TypedDict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep, require_admin
from app.domain import analytics, rbac
from app.persistence.models import Account, Loan, UserProfile

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/rebuild", status_code=200, dependencies=[Depends(require_admin)])
def rebuild_aggregates(
    household: HouseholdDep, session: SessionDep, actor: ActorDep, _profile: ProfileDep,
) -> dict[str, int]:
    """Recompute the whole aggregate table from raw data. Audited."""
    n = analytics.rebuild(session, household.id)
    emit(session, household.id, actor, "rebuild", "monthly_aggregate",
         0, {"months_written": n})
    return {"months_written": n}


@router.get("/monthly",
            response_model=list[schemas.MonthlyAggregateOut])
def monthly_series(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    months: Annotated[int, Query(ge=1, le=120)] = 12,
) -> list[schemas.MonthlyAggregateOut]:
    rows = analytics.series(session, household.id, months,
                            **_scope(session, household.id, profile))
    return [schemas.MonthlyAggregateOut.model_validate(r)
            for r in rows]


@router.get("/net-worth", response_model=schemas.NetWorthOut)
def net_worth_now(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
) -> schemas.NetWorthOut:
    from datetime import date
    return schemas.NetWorthOut(
        net_worth_pence=analytics.net_worth_at(
            session, household.id, date.today(), **_scope(session, household.id, profile)))


@router.get("/forecast",
            response_model=list[schemas.ForecastRowOut])
def expenditure_forecast(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    months: Annotated[int, Query(ge=1, le=36)] = 12,
    history_months: Annotated[int, Query(ge=1, le=60)] = 12,
) -> list[schemas.ForecastRowOut]:
    """Explainable per-category spend forecast (issue #113) — every
    row names its method, confidence class, and input window."""
    from app.domain import forecasting
    return [schemas.ForecastRowOut(
        category_id=r.category_id, month=r.month,
        amount_pence=r.amount_pence, method=r.method,
        confidence=r.confidence, input_start=r.input_start,
        input_end=r.input_end, input_months=r.input_months)
        for r in forecasting.forecast(
            session, household.id, months=months,
            history_months=history_months,
            account_ids=_scope(session, household.id, profile)["account_ids"])]


class _AnalyticsScope(TypedDict):
    account_ids: list[int]
    loan_ids: list[int]


def _scope(session: Session, household_id: int,
           profile: UserProfile | None) -> _AnalyticsScope:
    return {"account_ids": [a.id for a in session.scalars(select(Account).where(
                Account.household_id == household_id))
                if rbac.visible(a.owner_profile_id, profile)],
            "loan_ids": [loan.id for loan in session.scalars(select(Loan).where(
                Loan.household_id == household_id))
                if rbac.visible(loan.owner_profile_id, profile)]}
