"""Bank connections (issue #65): connect/rotate/disconnect flows +
status listing. Tokens are write-only input — the response model
has no field capable of carrying them. The token validator is
resolvable from ``app.state`` so the live Starling client (#66)
can replace the stdlib default transparently."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import banks, vault
from app.domain.banks import (
    ConnError,
    TokenInfo,
    TokenInvalid,
    TokenRejected,
    Validator,
)
from app.domain.vault import Keyring
from app.persistence.models import BankConnection, BankProvider

router = APIRouter(prefix="/banking", tags=["banking"])

API_BASE = "https://api.starlingbank.com"


def _starling_validate(token: str) -> TokenInfo:
    """Production validator — stdlib HTTP (no new deps): asks
    Starling which scopes the token actually holds."""
    req = urllib.request.Request(
        f"{API_BASE}/api/v2/identity/token",
        headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TokenInvalid(f"HTTP {e.code}") from e
        raise banks.ValidatorUnavailable(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise banks.ValidatorUnavailable(str(e)) from e
    return TokenInfo(scopes=frozenset(data.get("scopes", [])))


def _validator(request: Request) -> Validator:
    return getattr(request.app.state, "token_validator",
                   _starling_validate)


def _keyring(request: Request) -> Keyring:
    return vault.keyring(request.app.state.settings)


ValidatorDep = Annotated[Validator, Depends(_validator)]
RingDep = Annotated[Keyring, Depends(_keyring)]


@router.get("/connections",
            response_model=list[schemas.BankConnectionOut])
def list_connections(
    household: HouseholdDep, session: SessionDep,
) -> list[schemas.BankConnectionOut]:
    rows = session.scalars(select(BankConnection).where(
        BankConnection.household_id == household.id))
    return [schemas.BankConnectionOut.model_validate(c)
            for c in rows]


@router.post("/connections",
             response_model=schemas.BankConnectionOut, status_code=201)
def connect(
    body: schemas.BankConnectIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
    validate: ValidatorDep, ring: RingDep,
) -> schemas.BankConnectionOut:
    existing = session.scalar(select(BankConnection).where(
        BankConnection.household_id == household.id,
        BankConnection.provider == BankProvider(body.provider)))
    try:
        c = banks.connect(
            session, household.id, BankProvider(body.provider),
            body.token.get_secret_value(), validate, ring,
            existing=existing)
    except TokenInvalid as e:
        raise HTTPException(401, "token rejected by provider") from e
    except TokenRejected as e:
        raise HTTPException(422, str(e)) from e
    except ConnError as e:
        raise HTTPException(502, f"provider unreachable: {e}") from e
    emit(session, household.id, actor, "bank_connect",
         "bank_connection", c.id)
    return schemas.BankConnectionOut.model_validate(c)


def _conn(session: Session, household_id: int,
          cid: int) -> BankConnection:
    c = session.get(BankConnection, cid)
    if c is None or c.household_id != household_id:
        raise HTTPException(404, f"connection {cid} not found")
    return c


@router.post("/connections/{cid}/rotate",
             response_model=schemas.BankConnectionOut)
def rotate(
    cid: int, body: schemas.BankConnectIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
    validate: ValidatorDep, ring: RingDep,
) -> schemas.BankConnectionOut:
    c = _conn(session, household.id, cid)
    try:
        banks.rotate(session, c, body.token.get_secret_value(),
                     validate, ring)
    except TokenInvalid as e:
        raise HTTPException(401, "token rejected by provider") from e
    except TokenRejected as e:
        raise HTTPException(422, str(e)) from e
    except ConnError as e:
        raise HTTPException(502, f"provider unreachable: {e}") from e
    emit(session, household.id, actor, "bank_rotate",
         "bank_connection", c.id)
    return schemas.BankConnectionOut.model_validate(c)


@router.post("/connections/{cid}/disconnect",
             response_model=schemas.BankConnectionOut)
def disconnect(
    cid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, ring: RingDep,
) -> schemas.BankConnectionOut:
    c = _conn(session, household.id, cid)
    banks.disconnect(session, c, ring)
    emit(session, household.id, actor, "bank_disconnect",
         "bank_connection", c.id)
    return schemas.BankConnectionOut.model_validate(c)
