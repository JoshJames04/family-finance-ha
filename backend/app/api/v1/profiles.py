"""User profiles: household members + RBAC lifecycle (issues
#61, #28). Role changes and (de)activation require an admin actor
— except the bootstrap window, when no active admin exists yet."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import (
    ActorDep,
    HouseholdDep,
    ProfileDep,
    SessionDep,
)
from app.domain import rbac
from app.persistence.models import Role, UserProfile

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


def _active_admin_exists(session: Session, household_id: int) -> bool:
    return bool(session.scalar(
        select(func.count(UserProfile.id)).where(
            UserProfile.household_id == household_id,
            UserProfile.role == Role.admin,
            UserProfile.is_active)))


def _guard(actor_profile: UserProfile | None,
           session: Session, household_id: int) -> None:
    """Management gate: admin required, or open during bootstrap
    (no active admin yet — the first user must be able to promote)."""
    if not rbac.manage_profiles_allowed(
            actor_profile, _active_admin_exists(
                session, household_id)):
        raise HTTPException(403, "admin role required")


@router.patch("/{profile_id}",
              response_model=schemas.ProfileOut)
def update_profile(
    profile_id: int, body: schemas.ProfilePatch,
    household: HouseholdDep, session: SessionDep,
    actor: ActorDep, actor_profile: ProfileDep,
) -> schemas.ProfileOut:
    """Role change — admin-managed, audited with old→new role."""
    _guard(actor_profile, session, household.id)
    p = _get(session, household.id, profile_id)
    if body.role is not None and body.role != p.role:
        old = p.role.value
        p.role = body.role
        session.flush()
        emit(session, household.id, actor, "role_change",
             "user_profile", p.id,
             {"from": old, "to": p.role.value})
    if body.display_name is not None:
        p.display_name = body.display_name
        session.flush()
    return schemas.ProfileOut.model_validate(p)


@router.post("/{profile_id}/deactivate",
             response_model=schemas.ProfileOut)
def deactivate(
    profile_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
    actor_profile: ProfileDep,
) -> schemas.ProfileOut:
    """Immediate access loss — the next request with this profile
    is denied at resolve time."""
    _guard(actor_profile, session, household.id)
    p = _get(session, household.id, profile_id)
    if actor_profile is not None and p.id == actor_profile.id:
        raise HTTPException(422, "cannot deactivate yourself")
    p.is_active = False
    session.flush()
    emit(session, household.id, actor, "deactivate",
         "user_profile", p.id, {"display_name": p.display_name})
    return schemas.ProfileOut.model_validate(p)


@router.post("/{profile_id}/activate",
             response_model=schemas.ProfileOut)
def activate(
    profile_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
    actor_profile: ProfileDep,
) -> schemas.ProfileOut:
    _guard(actor_profile, session, household.id)
    p = _get(session, household.id, profile_id)
    p.is_active = True
    session.flush()
    emit(session, household.id, actor, "activate",
         "user_profile", p.id, {"display_name": p.display_name})
    return schemas.ProfileOut.model_validate(p)
