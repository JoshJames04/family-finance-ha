"""Transfers: create linked legs, list."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain.transfers import create_transfer
from app.persistence.models import Account, Transaction, Transfer

router = APIRouter(prefix="/transfers", tags=["transfers"])


def _leg_in_household(
    session: Session, household_id: int, txn_id: int
) -> Transaction:
    t = session.get(Transaction, txn_id)
    if t is None:
        raise HTTPException(404, f"transaction {txn_id} not found")
    a = session.get(Account, t.account_id)
    if a is None or a.household_id != household_id:
        raise HTTPException(404, f"transaction {txn_id} not found")
    return t


def _out(t: Transfer) -> schemas.TransferOut:
    return schemas.TransferOut(
        id=t.id, household_id=t.household_id, matched=t.matched,
        rule_ref=t.rule_ref, notes=t.notes,
        legs=[leg.id for leg in t.legs])


@router.get("", response_model=schemas.Page[schemas.TransferOut])
def list_transfers(
    household: HouseholdDep, session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> schemas.Page[schemas.TransferOut]:
    base = select(Transfer).where(Transfer.household_id == household.id)
    total = len(list(session.execute(base).scalars().all()))
    rows = session.execute(
        base.order_by(Transfer.id.desc())
        .offset((page - 1) * size).limit(size)
    ).scalars().all()
    return schemas.Page(items=[_out(t) for t in rows], total=total,
                        page=page, size=size)


@router.post("", response_model=schemas.TransferOut, status_code=201)
def create(
    body: schemas.TransferIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.TransferOut:
    debit = _leg_in_household(session, household.id,
                              body.debit_transaction_id)
    credit = _leg_in_household(session, household.id,
                               body.credit_transaction_id)
    t = create_transfer(session, household.id, debit, credit,
                        matched=body.matched, rule_ref=body.rule_ref,
                        notes=body.notes)
    emit(session, household.id, actor, "create", "transfer", t.id,
         {"legs": [debit.id, credit.id]})
    return _out(t)
