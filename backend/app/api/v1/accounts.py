"""Accounts: CRUD + archive/reopen + delete guard + balance."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import (
    ActorDep,
    HouseholdDep,
    ProfileDep,
    SessionDep,
)
from app.domain import accounts as accts
from app.domain import projection, rbac
from app.domain.accounts import current_balance
from app.persistence.models import Account, Role, UserProfile

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _get(
    session: Session, household_id: int, account_id: int,
    profile: UserProfile | None = None,
) -> Account:
    a = session.get(Account, account_id)
    if (a is None or a.household_id != household_id
            or not rbac.visible(a.owner_profile_id, profile)):
        raise HTTPException(404, f"account {account_id} not found")
    return a


def _write(a: Account, profile: UserProfile | None) -> None:
    """Mutation gate on a resolved resource (issue #32)."""
    if not rbac.write_allowed(a.owner_profile_id, profile):
        raise HTTPException(403, "role cannot mutate this scope")


@router.get("", response_model=schemas.Page[schemas.AccountOut])
def list_accounts(
    household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
    include_archived: Annotated[bool, Query()] = False,
) -> schemas.Page[schemas.AccountOut]:
    rows = (accts.all_accounts(session, household.id)
            if include_archived
            else accts.active_accounts(session, household.id))
    rows = [a for a in rows
            if rbac.visible(a.owner_profile_id, profile)]
    return schemas.Page(
        items=[schemas.AccountOut.model_validate(a) for a in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.post("", response_model=schemas.AccountOut, status_code=201)
def create_account(
    body: schemas.AccountIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.AccountOut:
    if not rbac.write_allowed(body.owner_profile_id, profile):
        raise HTTPException(403, "role cannot mutate this scope")
    a = accts.open_account(
        session, household.id, body.name, body.type,
        body.opening_balance.pence, body.opening_date,
        owner_profile_id=body.owner_profile_id,
        institution_ref=body.institution_ref,
    )
    emit(session, household.id, actor, "create", "account", a.id,
         {"name": a.name, "type": a.type.value})
    return schemas.AccountOut.model_validate(a)


@router.get("/{account_id}", response_model=schemas.AccountOut)
def get_account(
    account_id: int, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
) -> schemas.AccountOut:
    return schemas.AccountOut.model_validate(
        _get(session, household.id, account_id, profile))


@router.get("/{account_id}/balance")
def get_balance(
    account_id: int, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
) -> dict[str, int]:
    a = _get(session, household.id, account_id, profile)
    return {"account_id": a.id,
            "balance_pence": current_balance(session, a.id)}


@router.get("/{account_id}/projection",
            response_model=schemas.ProjectionOut)
def get_projection(
    account_id: int, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
    days: Annotated[int, Query(ge=1, le=731)] = 90,
) -> schemas.ProjectionOut:
    """Deterministic per-account daily projection + safe-to-spend
    (issue #74)."""
    a = _get(session, household.id, account_id, profile)
    return projection_out(projection.project(session, a, days))


def projection_out(p: projection.Projection) -> schemas.ProjectionOut:
    """Shared dataclass→schema mapper (scenarios diff two of these)."""
    return schemas.ProjectionOut(
        account_id=p.account_id,
        start_balance_pence=p.start_balance_pence,
        next_payday=p.next_payday,
        committed_until_payday_pence=p.committed_until_payday_pence,
        safe_to_spend_pence=p.safe_to_spend_pence,
        min_balance_pence=p.min_balance_pence,
        min_balance_date=p.min_balance_date,
        threshold_pence=p.threshold_pence,
        below_threshold=p.below_threshold,
        days=[schemas.ProjectionDayOut(
            date=d.date, movements_pence=d.movements_pence,
            balance_pence=d.balance_pence) for d in p.days],
    )


@router.post("/{account_id}/verify-balance",
             response_model=schemas.BalanceVerificationOut, status_code=201)
def verify_account_balance(
    account_id: int, body: schemas.BalanceVerifyIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.BalanceVerificationOut:
    a = _get(session, household.id, account_id, profile)
    _write(a, profile)
    v = accts.verify_balance(
        session, a, body.asserted_balance.pence, body.verified_date,
        correct=body.correct, note=body.note)
    emit(session, household.id, actor, "verify_balance", "account", a.id,
         {"verification_id": v.id, "variance_pence": v.variance_pence,
          "corrected": v.correction_txn_id is not None})
    return schemas.BalanceVerificationOut.model_validate(v)


@router.get("/{account_id}/verifications",
            response_model=schemas.Page[schemas.BalanceVerificationOut])
def account_verifications(
    account_id: int, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
) -> schemas.Page[schemas.BalanceVerificationOut]:
    a = _get(session, household.id, account_id, profile)
    rows = accts.verifications(session, a.id)
    return schemas.Page(
        items=[schemas.BalanceVerificationOut.model_validate(v)
               for v in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.patch("/{account_id}", response_model=schemas.AccountOut)
def patch_account(
    account_id: int, body: schemas.AccountPatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.AccountOut:
    a = _get(session, household.id, account_id, profile)
    _write(a, profile)
    changed = body.model_dump(exclude_unset=True)
    new_owner = changed.get("owner_profile_id")
    if "owner_profile_id" in changed and profile is not None:
        # Scope change: non-admin may only toggle own/None (issue #32).
        if profile.role != Role.admin and new_owner not in (
                None, profile.id):
            raise HTTPException(403, "cannot reassign ownership")
    for k, v in changed.items():
        setattr(a, k, v)
    emit(session, household.id, actor, "update", "account", a.id, changed)
    return schemas.AccountOut.model_validate(a)


@router.post("/{account_id}/archive", response_model=schemas.AccountOut)
def archive_account(
    account_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.AccountOut:
    a = _get(session, household.id, account_id, profile)
    _write(a, profile)
    accts.archive(session, a)
    emit(session, household.id, actor, "archive", "account", a.id)
    return schemas.AccountOut.model_validate(a)


@router.post("/{account_id}/reopen", response_model=schemas.AccountOut)
def reopen_account(
    account_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.AccountOut:
    a = _get(session, household.id, account_id, profile)
    _write(a, profile)
    accts.reopen(session, a)
    emit(session, household.id, actor, "reopen", "account", a.id)
    return schemas.AccountOut.model_validate(a)


@router.delete("/{account_id}", status_code=204)
def delete_account(
    account_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> None:
    a = _get(session, household.id, account_id, profile)
    _write(a, profile)
    accts.delete_account(session, a)  # raises 409 if non-empty
    emit(session, household.id, actor, "delete", "account", a.id)


@router.post("/{account_id}/merge-into/{target_id}",
             response_model=schemas.AccountOut)
def merge_account(
    account_id: int, target_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.AccountOut:
    src = _get(session, household.id, account_id, profile)
    tgt = _get(session, household.id, target_id, profile)
    _write(src, profile)
    accts.merge_accounts(session, src, tgt)
    emit(session, household.id, actor, "merge", "account", src.id,
         {"into": tgt.id})
    return schemas.AccountOut.model_validate(src)


@router.get("/{account_id}/transactions",
            response_model=schemas.Page[schemas.TransactionOut])
def account_transactions(
    account_id: int, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> schemas.Page[schemas.TransactionOut]:
    _get(session, household.id, account_id, profile)
    from app.persistence.models import Transaction
    base = select(Transaction).where(Transaction.account_id == account_id)
    total = len(list(session.execute(base).scalars().all()))
    rows = session.execute(
        base.order_by(Transaction.date.desc(), Transaction.id.desc())
        .offset((page - 1) * size).limit(size)
    ).scalars().all()
    return schemas.Page(
        items=[schemas.TransactionOut.model_validate(t) for t in rows],
        total=total, page=page, size=size)
