"""Analytics: materialised monthly aggregates + time-series reads."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/rebuild", status_code=200)
def rebuild_aggregates(
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> dict[str, int]:
    """Recompute the whole aggregate table from raw data. Audited."""
    n = analytics.rebuild(session, household.id)
    emit(session, household.id, actor, "rebuild", "monthly_aggregate",
         0, {"months_written": n})
    return {"months_written": n}


@router.get("/monthly",
            response_model=list[schemas.MonthlyAggregateOut])
def monthly_series(
    household: HouseholdDep, session: SessionDep,
    months: Annotated[int, Query(ge=1, le=120)] = 12,
) -> list[schemas.MonthlyAggregateOut]:
    rows = analytics.series(session, household.id, months)
    return [schemas.MonthlyAggregateOut.model_validate(r)
            for r in rows]


@router.get("/net-worth", response_model=schemas.NetWorthOut)
def net_worth_now(
    household: HouseholdDep, session: SessionDep,
) -> schemas.NetWorthOut:
    from datetime import date
    return schemas.NetWorthOut(
        net_worth_pence=analytics.net_worth_at(
            session, household.id, date.today()))


@router.get("/forecast",
            response_model=list[schemas.ForecastRowOut])
def expenditure_forecast(
    household: HouseholdDep, session: SessionDep,
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
            history_months=history_months)]
