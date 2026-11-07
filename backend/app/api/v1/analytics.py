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
