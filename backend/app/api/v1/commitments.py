"""Recurring commitments: list/create/archive + materialise."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import schedule
from app.domain.recurring import materialise
from app.persistence.models import Account, RecurringCommitment

router = APIRouter(prefix="/commitments", tags=["commitments"])


def _get(
    session: Session, household_id: int, cid: int
) -> RecurringCommitment:
    c = session.get(RecurringCommitment, cid)
    if c is None or c.household_id != household_id:
        raise HTTPException(404, f"commitment {cid} not found")
    return c


@router.get("", response_model=schemas.Page[schemas.CommitmentOut])
def list_commitments(
    household: HouseholdDep, session: SessionDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> schemas.Page[schemas.CommitmentOut]:
    stmt = select(RecurringCommitment).where(
        RecurringCommitment.household_id == household.id)
    if not include_inactive:
        stmt = stmt.where(RecurringCommitment.active.is_(True))
    rows = list(session.execute(stmt).scalars().all())
    return schemas.Page(
        items=[schemas.CommitmentOut.model_validate(c) for c in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.post("", response_model=schemas.CommitmentOut, status_code=201)
def create_commitment(
    body: schemas.CommitmentIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.CommitmentOut:
    a = session.get(Account, body.account_id)
    if a is None or a.household_id != household.id:
        raise HTTPException(404, f"account {body.account_id} not found")
    if (body.amount is None) == (body.formula is None):
        raise HTTPException(
            422, "exactly one of amount or formula is required")
    c = RecurringCommitment(
        household_id=household.id, name=body.name, account_id=a.id,
        category_id=body.category_id,
        amount_pence=body.amount.pence if body.amount else None,
        formula=body.formula, frequency=body.frequency,
        day_of_month=body.day_of_month, next_date=body.next_date,
        end_date=body.end_date, occurrence_count=body.occurrence_count,
        notes=body.notes, generated_count=0,
    )
    session.add(c)
    session.flush()
    schedule.rematerialize_commitment(session, c)
    emit(session, household.id, actor, "create",
         "recurring_commitment", c.id, {"name": c.name})
    return schemas.CommitmentOut.model_validate(c)


@router.post("/{cid}/materialise", response_model=schemas.MaterialiseResult)
def materialise_commitment(
    cid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, horizon: Annotated[date, Query()],
) -> schemas.MaterialiseResult:
    c = _get(session, household.id, cid)
    made = materialise(session, c, horizon)
    schedule.rematerialize_commitment(session, c)
    emit(session, household.id, actor, "materialise",
         "recurring_commitment", c.id,
         {"created": [t.id for t in made]})
    return schemas.MaterialiseResult(
        created=[t.id for t in made], next_date=c.next_date)


@router.delete("/{cid}", status_code=204)
def deactivate_commitment(
    cid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> None:
    c = _get(session, household.id, cid)
    c.active = False
    schedule.rematerialize_commitment(session, c)  # drops future rows
    emit(session, household.id, actor, "deactivate",
         "recurring_commitment", c.id)
