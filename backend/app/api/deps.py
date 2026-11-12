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

from app.persistence.models import Household, Role, UserProfile
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
    request: Request,
    x_actor_id: Annotated[str | None, Header()] = None,
) -> str:
    if getattr(request.state, "service_authenticated", False):
        return "ha-service"
    return x_actor_id or "anonymous"


ActorDep = Annotated[str, Depends(actor_header)]


def resolve_profile(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    household: HouseholdDep,
    x_profile_id: Annotated[int | None, Header()] = None,
) -> UserProfile | None:
    """Member identity. The middleware-resolved profile
    (supervisor headers in ingress mode, session cookie in
    standalone) wins. ``X-Profile-Id`` is the standalone/dev compat
    path — it is disabled under ingress, where only supervisor
    identity may resolve (spoof hole, issue #29)."""
    pid = getattr(request.state, "profile_id", None)
    if pid is None and request.app.state.settings.ingress:
        if getattr(request.state, "service_authenticated", False):
            return None
        if session.scalar(select(UserProfile.id).limit(1)) is not None:
            raise HTTPException(401, "ingress identity required")
        return None
    if pid is None:
        if x_profile_id is None:
            return None
        pid = x_profile_id
    p = session.get(UserProfile, pid)
    if p is None or p.household_id != household.id:
        raise HTTPException(404, f"profile {pid} not found")
    if not p.is_active:
        raise HTTPException(403, "profile is deactivated")
    return p


ProfileDep = Annotated["UserProfile | None", Depends(resolve_profile)]


def require_admin(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    """Admin gate: the resolved profile's role, or the legacy
    ``X-Admin-Token`` header matching ``FF_ADMIN_TOKEN``. Identity
    comes from the middleware (supervisor headers / session cookie)
    or — standalone only — the ``X-Profile-Id`` compat header, which
    stays disabled under ingress. Fail-closed either way (issue #29)."""
    pid = getattr(request.state, "profile_id", None)
    if pid is None and not request.app.state.settings.ingress:
        raw = request.headers.get("x-profile-id")
        if raw is not None and raw.isdigit():
            pid = int(raw)
    if pid is not None:
        p = session.get(UserProfile, pid)
        if p is not None and p.is_active and p.role == Role.admin:
            return
    settings = request.app.state.settings
    expected = (settings.admin_token.get_secret_value()
                if settings.admin_token is not None else None)
    given = request.headers.get("x-admin-token")
    ok = (
        expected is not None and expected != ""
        and given is not None
        and hmac.compare_digest(given, expected)
    )
    if not ok:
        raise HTTPException(403, "admin token required")


AdminDep = Annotated[None, Depends(require_admin)]
