"""Authentication enforcement (issue #29, spike #27, ADR-0005).

Every request resolves an identity into ``request.state.profile_id``:

- **ingress mode** (``FF_INGRESS=true``): remote-user headers are
  trusted ONLY from ``172.30.32.2`` — anywhere else they're a spoof
  attempt → 401, per the Supervisor strip-then-inject contract.
  ``X-Profile-Id`` is ignored in this mode.
- **standalone mode**: ``ff_session`` cookie → live AuthSession row
  (expired/revoked/mismatched-role → anonymous, not error); the
  ``X-Profile-Id`` compat header still resolves in deps.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint

from app.api import schemas
from app.api.deps import SessionDep
from app.core import security
from app.core.settings import Settings
from app.persistence.models import (
    AuthSession,
    Household,
    Role,
    UserProfile,
)

SUPERVISOR_IP = "172.30.32.2"
REMOTE_HEADERS = frozenset({
    "x-remote-user-id", "x-remote-user-name",
    "x-remote-user-display-name", "x-remote-user-groups"})


def has_remote_headers(request: Request) -> bool:
    return any(h in request.headers for h in REMOTE_HEADERS)


def is_spoof(request: Request) -> bool:
    """Remote-user headers from a non-supervisor source."""
    if not has_remote_headers(request):
        return False
    client = request.client
    return client is None or client.host != SUPERVISOR_IP


def _ha_profile(session: Session, household: Household,
                request: Request) -> UserProfile | None:
    """Provision-or-refresh a profile from trusted ingress
    headers. First-ever profile in the household becomes admin
    (bootstrap AC)."""
    uid = request.headers.get("x-remote-user-id")
    if not uid:
        return None
    p = session.scalar(select(UserProfile).where(
        UserProfile.ha_user_id == uid))
    display = request.headers.get(
        "x-remote-user-display-name") or request.headers.get(
        "x-remote-user-name") or "HA user"
    if p is None:
        first = session.scalar(
            select(UserProfile).where(
                UserProfile.household_id == household.id).limit(1))
        p = UserProfile(
            household_id=household.id, ha_user_id=uid,
            display_name=display,
            role=Role.member if first is not None else Role.admin)
        session.add(p)
        session.flush()
    elif p.display_name != display:
        p.display_name = display          # mutable decoration
        session.flush()
    return p


def _session_profile(session: Session,
                     request: Request) -> UserProfile | None:
    token = request.cookies.get(security.SESSION_COOKIE)
    if not token:
        return None
    s = session.scalar(select(AuthSession).where(
        AuthSession.token == token,
        AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > datetime.now()))
    if s is None:
        return None
    p = session.get(UserProfile, s.profile_id)
    # Session dies when the issuing privilege changes.
    if p is None or not p.is_active or p.role.value != s.role_at_issue:
        return None
    return p


def resolve_identity(request: Request,
                     session: Session) -> UserProfile | None:
    """The single resolution path — returns the profile or None.
    ``X-Profile-Id`` is intentionally NOT handled here: that compat
    path lives in ``deps.resolve_profile`` and is disabled under
    ingress, where only supervisor identity may resolve."""
    settings = request.app.state.settings
    if is_spoof(request):
        raise HTTPException(401, "untrusted remote-user headers")
    if settings.ingress:
        household = session.scalar(
            select(Household).order_by(Household.id).limit(1))
        if household is not None:
            return _ha_profile(session, household, request)
        return None
    return _session_profile(session, request)


async def auth_middleware(
        request: Request,
        call_next: RequestResponseEndpoint) -> Response:
    """Resolve identity for every request; spoofed remote-user
    headers → 401 before routing. Only the profile *id* is stored —
    the ORM object would detach when this session closes."""
    if is_spoof(request):
        return Response(status_code=401)
    engine = getattr(request.app.state, "engine", None)
    if engine is not None and (has_remote_headers(request)
                               or security.SESSION_COOKIE
                               in request.cookies):
        with Session(engine) as s:
            p = resolve_identity(request, s)
            request.state.profile_id = p.id if p is not None else None
            s.commit()
    return await call_next(request)


def current_profile(request: Request,
                    session: SessionDep) -> UserProfile | None:
    pid = getattr(request.state, "profile_id", None)
    if pid is None:
        return None
    p = session.get(UserProfile, pid)
    return p if p is not None and p.is_active else None


CurrentDep = Annotated[UserProfile | None, Depends(current_profile)]


def require_profile(profile: CurrentDep) -> UserProfile:
    """Deny-by-default dep for routes needing an identity."""
    if profile is None or not profile.is_active:
        raise HTTPException(401, "authentication required")
    return profile


RequiredDep = Annotated[UserProfile, Depends(require_profile)]


def require_role(*roles: Role
                 ) -> Callable[[UserProfile], UserProfile]:
    """Role gate — ``require_role(Role.admin)`` denies members."""

    def dep(profile: RequiredDep) -> UserProfile:
        if profile.role not in roles:
            raise HTTPException(403, f"role must be one of "
                                   f"{[r.value for r in roles]}")
        return profile
    dep._ff_auth = True  # type: ignore[attr-defined]
    return dep


# ---- standalone login/logout + throttling ----

router = APIRouter(prefix="/auth", tags=["auth"])
_attempts: dict[str, list[float]] = {}
_ATTEMPT_LIMIT = 5
_ATTEMPT_WINDOW = 60.0


def _throttle(request: Request) -> None:
    """Naive per-IP limiter on auth-adjacent endpoints — 5/min."""
    import time
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    hits = [t for t in _attempts.get(ip, []) if now - t
            < _ATTEMPT_WINDOW]
    if len(hits) >= _ATTEMPT_LIMIT:
        raise HTTPException(429, "too many attempts — try later")
    hits.append(now)
    _attempts[ip] = hits


@router.post("/login")
def login(request: Request, response: Response,
          body: schemas.LoginIn,
          session: SessionDep) -> dict[str, object]:
    """Standalone login — local password verify, then a server-side
    session in an HttpOnly+SameSite cookie."""
    _throttle(request)
    p = session.scalar(select(UserProfile).where(
        UserProfile.display_name == body.username,
        UserProfile.is_active))
    if p is None or not security.verify_password(
            body.password, p.password_hash):
        raise HTTPException(401, "invalid credentials")
    s = AuthSession(profile_id=p.id, token=security.new_token(),
                    role_at_issue=p.role.value,
                    expires_at=security.session_expiry())
    session.add(s)
    session.flush()
    response.set_cookie(
        security.SESSION_COOKIE, s.token, httponly=True,
        samesite="strict", secure=settings_secure(request),
        max_age=security.SESSION_HOURS * 3600)
    return {"profile_id": p.id, "role": p.role.value,
            "display_name": p.display_name}


def settings_secure(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    return settings.env == "prod"


@router.get("/me")
def me(profile: RequiredDep) -> dict[str, object]:
    """The resolved identity — the deny-by-default probe used by
    the coverage and lifecycle tests."""
    return {"id": profile.id, "display_name": profile.display_name,
            "role": profile.role.value,
            "household_id": profile.household_id}


@router.post("/logout")
def logout(request: Request, response: Response,
           session: SessionDep) -> dict[str, bool]:
    token = request.cookies.get(security.SESSION_COOKIE)
    if token:
        s = session.scalar(select(AuthSession).where(
            AuthSession.token == token))
        if s is not None and s.revoked_at is None:
            s.revoked_at = datetime.now()
            session.flush()
    response.delete_cookie(security.SESSION_COOKIE)
    return {"ok": True}


def revoke_sessions_for(session: Session, profile_id: int) -> None:
    """Role change/deactivation kills live sessions (AC)."""
    for s in session.scalars(select(AuthSession).where(
            AuthSession.profile_id == profile_id,
            AuthSession.revoked_at.is_(None))):
        s.revoked_at = datetime.now()
    session.flush()
