"""Recurring commitments: list/create/archive + materialise."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import schedule
from app.domain.recurring import advance, materialise
from app.persistence.models import (
    Account,
    ExpectedMovement,
    ExpectedSource,
    RecurringCommitment,
)

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


@router.patch("/{cid}", response_model=schemas.CommitmentOut)
def update_commitment(
    cid: int, body: schemas.CommitmentPatch, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.CommitmentOut:
    """Edit a commitment. amount/formula remain mutually exclusive:
    sending one clears the other. Schedule changes regenerate future
    expected rows; past generated occurrences are untouched."""
    c = _get(session, household.id, cid)
    fields = body.model_dump(exclude_unset=True)
    if "amount" in fields:
        c.amount_pence = body.amount.pence if body.amount else None
        if body.amount is not None:
            c.formula = None
    if "formula" in fields:
        c.formula = body.formula
        if body.formula is not None:
            c.amount_pence = None
    if "account_id" in fields:
        a = session.get(Account, fields["account_id"])
        if a is None or a.household_id != household.id:
            raise HTTPException(404, "account not found")
        c.account_id = a.id
    for key in ("name", "category_id", "frequency", "day_of_month",
                "next_date", "end_date", "occurrence_count", "notes"):
        if key in fields:
            setattr(c, key, fields[key])
    if (c.amount_pence is None) == (c.formula is None):
        raise HTTPException(
            422, "exactly one of amount or formula is required")
    session.flush()
    schedule.rematerialize_commitment(session, c)
    emit(session, household.id, actor, "update",
         "recurring_commitment", c.id, {"fields": sorted(fields)})
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
    actor: ActorDep, permanent: Annotated[bool, Query()] = False,
) -> None:
    """Pause (default, reversible via /activate) or permanently remove
    a commitment. Permanent delete is only allowed when the commitment
    never generated a transaction — history-bearing bills stay on the
    register (issue #321)."""
    c = _get(session, household.id, cid)
    if permanent:
        if c.generated_count:
            raise HTTPException(
                422, "commitment has generated transactions — pause "
                     "instead of deleting")
        session.execute(delete(ExpectedMovement).where(
            ExpectedMovement.source_type == ExpectedSource.commitment,
            ExpectedMovement.source_id == c.id))
        session.delete(c)
        emit(session, household.id, actor, "delete",
             "recurring_commitment", c.id)
        return
    c.active = False
    schedule.rematerialize_commitment(session, c)  # drops future rows
    emit(session, household.id, actor, "deactivate",
         "recurring_commitment", c.id)


@router.post("/{cid}/activate", response_model=schemas.CommitmentOut)
def activate_commitment(
    cid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> schemas.CommitmentOut:
    """Resume a paused commitment. Rolls next_date forward past today so
    a paused bill doesn't emit a burst of backdated occurrences."""
    c = _get(session, household.id, cid)
    c.active = True
    today = date.today()
    anchor_dom = c.day_of_month if c.day_of_month is not None \
        else c.next_date.day
    while c.next_date < today:
        c.next_date = advance(c.next_date, c.frequency, anchor_dom)
    schedule.rematerialize_commitment(session, c)
    emit(session, household.id, actor, "activate",
         "recurring_commitment", c.id)
    return schemas.CommitmentOut.model_validate(c)
