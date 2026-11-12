"""One authorization boundary for employment and private payslip routes."""
from fastapi import HTTPException, Request

from app.api.deps import HouseholdDep, ProfileDep, SessionDep
from app.domain import rbac
from app.persistence.models import Employment


def authorize_employment(request: Request, household: HouseholdDep,
                         session: SessionDep, profile: ProfileDep) -> None:
    raw = request.path_params.get("emp_id")
    if raw is None:
        return
    try:
        emp_id = int(raw)
    except ValueError as error:
        raise HTTPException(404, "employment not found") from error
    employment = session.get(Employment, emp_id)
    if (employment is None or employment.household_id != household.id
            or (profile is not None
                and not rbac.visible(employment.profile_id, profile))):
        raise HTTPException(404, "employment not found")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not rbac.write_allowed(
            employment.profile_id, profile):
        raise HTTPException(403, "role cannot mutate this scope")
