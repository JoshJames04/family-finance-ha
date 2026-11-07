"""Request-scoped dependencies.

Pre-auth placeholder: household scoping uses the optional
``X-Household-Id`` header, defaulting to the first household (the
single-household self-hosted norm). Epic 2 replaces this with real
authenticated scoping — every route already funnels through it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import Household
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
