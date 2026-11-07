"""Household-level forecast surfaces: overdraft warnings (#75)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api import schemas
from app.api.deps import HouseholdDep, SessionDep
from app.domain import projection

router = APIRouter(prefix="/forecast", tags=["forecast"])


@router.get("/warnings",
            response_model=list[schemas.OverdraftWarningOut])
def overdraft_warnings(
    household: HouseholdDep, session: SessionDep,
    days: Annotated[int, Query(ge=1, le=731)] = 90,
) -> list[schemas.OverdraftWarningOut]:
    """Projected floor breaches with suggested rescue sources —
    computed fresh so warnings resolve when inputs change (#75)."""
    return [schemas.OverdraftWarningOut(
        account_id=w.account_id, name=w.name,
        first_dip_date=w.first_dip_date,
        floor_pence=w.floor_pence,
        min_balance_pence=w.min_balance_pence,
        deficit_pence=w.deficit_pence,
        suggested_sources=[
            schemas.WarningSourceOut(
                account_id=s.account_id, name=s.name,
                available_pence=s.available_pence)
            for s in w.suggested_sources])
        for w in projection.warnings(session, household.id, days)]
