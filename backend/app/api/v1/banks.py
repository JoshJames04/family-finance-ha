"""Bank connections (issue #65): connect/rotate/disconnect flows +
status listing. Tokens are write-only input — the response model
has no field capable of carrying them. The token validator is
resolvable from ``app.state`` so the live Starling client (#66)
can replace the stdlib default transparently."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import banks, discovery, vault
from app.domain.banks import (
    ConnError,
    TokenInvalid,
    TokenRejected,
    Validator,
)
from app.domain.discovery import BankClient, DiscoveryError
from app.domain.vault import Keyring
from app.integrations.starling import StarlingClient
from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    BankProvider,
)

router = APIRouter(prefix="/banking", tags=["banking"])


def _client(request: Request) -> BankClient:
    return getattr(request.app.state, "bank_client",
                   StarlingClient())


def _validator(request: Request) -> Validator:
    return getattr(request.app.state, "token_validator",
                   StarlingClient().validate_token)


def _keyring(request: Request) -> Keyring:
    return vault.keyring(request.app.state.settings)


ValidatorDep = Annotated[Validator, Depends(_validator)]
RingDep = Annotated[Keyring, Depends(_keyring)]
ClientDep = Annotated[BankClient, Depends(_client)]


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


@router.post("/connections/{cid}/discover",
             response_model=list[schemas.BankMappingOut])
def discover(
    cid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, ring: RingDep, client: ClientDep,
) -> list[schemas.BankMappingOut]:
    """Re-runnable, idempotent discovery — proposes/renames/closes
    mappings from the provider's account list."""
    c = _conn(session, household.id, cid)
    try:
        token = banks.token(session, c, ring)
        rows = discovery.discover(session, c, client, token)
    except ConnError as e:
        raise HTTPException(502, str(e)) from e
    emit(session, household.id, actor, "bank_discover",
         "bank_connection", c.id)
    return [schemas.BankMappingOut.model_validate(m) for m in rows]


def _mapping(session: Session, household_id: int,
             mid: int) -> BankAccountMapping:
    m = session.get(BankAccountMapping, mid)
    if m is None or m.household_id != household_id:
        raise HTTPException(404, f"mapping {mid} not found")
    return m


@router.get("/mappings",
            response_model=list[schemas.BankMappingOut])
def list_mappings(
    household: HouseholdDep, session: SessionDep,
) -> list[schemas.BankMappingOut]:
    rows = session.scalars(select(BankAccountMapping).where(
        BankAccountMapping.household_id == household.id))
    return [schemas.BankMappingOut.model_validate(m) for m in rows]


@router.post("/mappings/{mid}/link",
             response_model=schemas.BankMappingOut)
def link(
    mid: int, body: schemas.MappingLinkIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.BankMappingOut:
    """Confirm a proposal — link an existing internal account or
    mint one when create_account is set."""
    m = _mapping(session, household.id, mid)
    if body.create_account:
        from datetime import date
        acct = Account(household_id=household.id,
                       name=m.external_label, type="personal",
                       opening_balance_pence=m.balance_pence or 0,
                       opening_date=date.today())
        session.add(acct)
        session.flush()
    else:
        if body.account_id is None:
            raise HTTPException(
                422, "account_id or create_account required")
        found = session.get(Account, body.account_id)
        if found is None or found.household_id != household.id:
            raise HTTPException(404, "account not found")
        acct = found
    try:
        discovery.link(session, m, acct)
    except DiscoveryError as e:
        raise HTTPException(422, str(e)) from e
    emit(session, household.id, actor, "bank_mapping_link",
         "bank_account_mapping", m.id)
    return schemas.BankMappingOut.model_validate(m)


@router.post("/mappings/{mid}/ignore",
             response_model=schemas.BankMappingOut)
def ignore(
    mid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> schemas.BankMappingOut:
    m = _mapping(session, household.id, mid)
    try:
        discovery.ignore(session, m)
    except DiscoveryError as e:
        raise HTTPException(422, str(e)) from e
    emit(session, household.id, actor, "bank_mapping_ignore",
         "bank_account_mapping", m.id)
    return schemas.BankMappingOut.model_validate(m)


@router.post("/connections/{cid}/sync-balances")
def sync_balances(
    cid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep, ring: RingDep, client: ClientDep,
) -> dict[str, int]:
    """Point-in-time balance refresh on linked mappings — never
    writes to the ledger."""
    c = _conn(session, household.id, cid)
    try:
        token = banks.token(session, c, ring)
        n = discovery.sync_balances(session, c, client, token)
    except ConnError as e:
        raise HTTPException(502, str(e)) from e
    emit(session, household.id, actor, "bank_balance_sync",
         "bank_connection", c.id)
    return {"synced": n}
