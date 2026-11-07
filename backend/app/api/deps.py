"""Request-scoped dependencies.

Pre-auth placeholder: household scoping uses the optional
``X-Household-Id`` header, defaulting to the first household (the
single-household self-hosted norm). Epic 2 replaces this with real
authenticated scoping — every route already funnels through it.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import Household, UserProfile
from app.persistence.session import get_session


def resolve_household(
    session: Annotated[Session, Depends(get_session)],
    x_household_id: Annotated[int | None, Header()] = None,
) -> Household:
    if x_household_id is not None:
        h = session.get(Household, x_household_id)
        if h is None:
            raise HTTPException(404, f"household {x_household_id} not found")
        return h
    h = session.execute(select(Household).limit(1)).scalar_one_or_none()
    if h is None:
        raise HTTPException(404, "no household exists")
    return h


HouseholdDep = Annotated[Household, Depends(resolve_household)]
SessionDep = Annotated[Session, Depends(get_session)]


def actor_header(
    x_actor_id: Annotated[str | None, Header()] = None,
) -> str:
    return x_actor_id or "anonymous"


ActorDep = Annotated[str, Depends(actor_header)]


def resolve_profile(
    session: Annotated[Session, Depends(get_session)],
    household: HouseholdDep,
    x_profile_id: Annotated[int | None, Header()] = None,
) -> UserProfile | None:
    """Optional member identity (pre-auth placeholder, like
    X-Household-Id). None = anonymous → sees shared resources only.
    A supplied id must resolve to an active profile in this household."""
    from app.persistence.models import UserProfile
    if x_profile_id is None:
        return None
    p = session.get(UserProfile, x_profile_id)
    if p is None or p.household_id != household.id:
        raise HTTPException(404, f"profile {x_profile_id} not found")
    if not p.is_active:
        raise HTTPException(403, "profile is deactivated")
    return p


ProfileDep = Annotated["UserProfile | None", Depends(resolve_profile)]


def require_admin(request: Request) -> None:
    """Pre-RBAC admin gate (issue #24): the ``X-Admin-Token`` header must
    match ``FF_ADMIN_TOKEN``. Fail-closed: when no token is configured
    every admin route refuses. Epic 2 replaces this with real roles."""
    settings = request.app.state.settings
    expected = settings.admin_token
    given = request.headers.get("x-admin-token")
    ok = (
        expected is not None and given is not None
        and hmac.compare_digest(given, expected.get_secret_value())
    )
    if not ok:
        raise HTTPException(403, "admin token required")


AdminDep = Annotated[None, Depends(require_admin)]
