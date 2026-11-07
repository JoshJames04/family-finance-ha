"""User profiles: household members (issue #61 scoping support)."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.persistence.models import UserProfile

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _get(session: Session, household_id: int,
         profile_id: int) -> UserProfile:
    p = session.get(UserProfile, profile_id)
    if p is None or p.household_id != household_id:
        raise HTTPException(404, "profile not found")
    return p


@router.get("", response_model=schemas.Page[schemas.ProfileOut])
def list_profiles(
    household: HouseholdDep, session: SessionDep,
) -> schemas.Page[schemas.ProfileOut]:
    rows = session.execute(
        select(UserProfile).where(
            UserProfile.household_id == household.id)
    ).scalars().all()
    return schemas.Page(
        items=[schemas.ProfileOut.model_validate(p) for p in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.post("", response_model=schemas.ProfileOut, status_code=201)
def create_profile(
    body: schemas.ProfileIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.ProfileOut:
    p = UserProfile(household_id=household.id,
                    display_name=body.display_name, role=body.role,
                    ha_user_id=body.ha_user_id)
    session.add(p)
    session.flush()
    emit(session, household.id, actor, "create", "user_profile", p.id,
         {"display_name": p.display_name, "role": p.role.value})
    return schemas.ProfileOut.model_validate(p)
