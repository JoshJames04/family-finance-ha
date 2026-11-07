"""Expected movements: the dated forecast stream + manual items."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import schedule
from app.persistence.models import ExpectedMovement, ExpectedSource

router = APIRouter(prefix="/expected-movements",
                   tags=["expected-movements"])


@router.get("", response_model=list[schemas.ExpectedMovementOut])
def list_movements(
    household: HouseholdDep, session: SessionDep,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
    account_id: int | None = None,
) -> list[schemas.ExpectedMovementOut]:
    return [schemas.ExpectedMovementOut.model_validate(m)
            for m in schedule.stream(
                session, household.id, start=start, end=end,
                account_id=account_id)]


@router.post("", response_model=schemas.ExpectedMovementOut,
             status_code=201)
def create_manual(
    body: schemas.ExpectedMovementIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.ExpectedMovementOut:
    m = schedule.add_manual(
        session, household.id, account_id=body.account_id,
        expected_date=body.expected_date,
        amount_pence=body.amount.pence, label=body.label)
    emit(session, household.id, actor, "create", "expected_movement",
         m.id, {"expected_date": str(m.expected_date)})
    return schemas.ExpectedMovementOut.model_validate(m)


@router.delete("/{mid}", status_code=204)
def delete_manual(
    mid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> None:
    m = session.get(ExpectedMovement, mid)
    if m is None or m.household_id != household.id:
        raise HTTPException(404, f"expected movement {mid} not found")
    if m.source_type is not ExpectedSource.manual:
        raise HTTPException(
            409, "only manual expected movements can be deleted — "
            "sourced rows regenerate from their source")
    session.execute(delete(ExpectedMovement).where(
        ExpectedMovement.id == mid))
    emit(session, household.id, actor, "delete", "expected_movement",
         mid, {})


@router.post("/materialise", status_code=202)
def rematerialise(
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> dict[str, int]:
    """Bulk regenerate future rows from all sources (idempotent)."""
    n = schedule.materialize_all(session, household)
    emit(session, household.id, actor, "materialise",
         "expected_movement", household.id, {"count": n})
    return {"materialised": n}
