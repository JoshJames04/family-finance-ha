"""Household bootstrap + listing."""

from fastapi import APIRouter
from sqlalchemy import select

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, SessionDep
from app.persistence.models import Household

router = APIRouter(prefix="/households", tags=["households"])


@router.get("", response_model=schemas.Page[schemas.HouseholdOut])
def list_households(session: SessionDep) -> schemas.Page[schemas.HouseholdOut]:
    rows = list(session.execute(select(Household)).scalars().all())
    return schemas.Page(
        items=[schemas.HouseholdOut.model_validate(h) for h in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.post("", response_model=schemas.HouseholdOut, status_code=201)
def create_household(
    body: schemas.HouseholdIn, session: SessionDep, actor: ActorDep,
) -> schemas.HouseholdOut:
    h = Household(name=body.name)
    session.add(h)
    session.flush()
    emit(session, h.id, actor, "create", "household", h.id,
         {"name": h.name})
    return schemas.HouseholdOut.model_validate(h)
