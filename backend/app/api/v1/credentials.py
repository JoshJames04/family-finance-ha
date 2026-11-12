"""Credential vault API (issue #30).

The surface is ``credential_ref`` only: store under a ref, list
refs, delete. Plaintext values are write-only and are never carried
by any response model — there is no retrieve endpoint; decryption
is a server-side concern for the consumer (e.g. Starling client).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, AdminDep, HouseholdDep, SessionDep
from app.domain import vault
from app.domain.vault import Keyring, VaultError
from app.persistence.models import VaultEntry

router = APIRouter(prefix="/credentials", tags=["credentials"])


def _keyring(request: Request) -> Keyring:
    return vault.keyring(request.app.state.settings)


RingDep = Annotated[Keyring, Depends(_keyring)]


@router.get("", response_model=list[schemas.CredentialRefOut])
def list_refs(
    household: HouseholdDep, session: SessionDep, _admin: AdminDep,
) -> list[schemas.CredentialRefOut]:
    rows = session.scalars(
        select(VaultEntry)
        .where(VaultEntry.household_id == household.id)
        .order_by(VaultEntry.ref))
    return [schemas.CredentialRefOut.model_validate(e) for e in rows]


@router.post("", response_model=schemas.CredentialRefOut,
             status_code=201)
def store(
    body: schemas.CredentialStoreIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, ring: RingDep, _admin: AdminDep,
) -> schemas.CredentialRefOut:
    try:
        e = vault.store(session, household.id, body.ref,
                        body.secret.get_secret_value().encode(), ring)
    except VaultError as e2:
        raise HTTPException(422, str(e2)) from e2
    emit(session, household.id, actor, "credential_store",
         "vault_entry", e.id)
    return schemas.CredentialRefOut.model_validate(e)


@router.delete("/{entry_id}", status_code=204)
def purge(entry_id: int, household: HouseholdDep,
          session: SessionDep, actor: ActorDep, _admin: AdminDep) -> None:
    e = session.get(VaultEntry, entry_id)
    if e is None or e.household_id != household.id:
        raise HTTPException(404, f"credential {entry_id} not found")
    emit(session, household.id, actor, "credential_purge",
         "vault_entry", e.id)
    vault.delete(session, e)
