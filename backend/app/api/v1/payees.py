"""Canonical payees (issue #276): distinct payee list plus rename/merge.

Payees are free text on transactions; this router derives the canonical
list and provides bulk rename (merge = rename onto an existing name).
Reconciled or locked-period rows keep the ledger immutable — they are
skipped and reported rather than silently rewritten."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain.periods import assert_editable
from app.persistence.models import Account, Transaction

router = APIRouter(prefix="/payees", tags=["payees"])


@router.get("", response_model=list[schemas.PayeeOut])
def list_payees(
    household: HouseholdDep, session: SessionDep,
    q: Annotated[str | None, Query()] = None,
) -> list[schemas.PayeeOut]:
    """Distinct payees with transaction counts, most-used first."""
    stmt = (
        select(Transaction.payee, func.count().label("n"))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household.id)
        .group_by(Transaction.payee)
        .order_by(func.count().desc(), Transaction.payee))
    if q:
        stmt = stmt.where(Transaction.payee.ilike(f"%{q}%"))
    return [schemas.PayeeOut(payee=p, transaction_count=n)
            for p, n in session.execute(stmt).all()]


@router.post("/rename", response_model=schemas.PayeeRenameOut)
def rename_payee(
    body: schemas.PayeeRenameIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.PayeeRenameOut:
    """Rename/merge a payee across editable transactions."""
    rows = session.scalars(
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household.id,
               Transaction.payee == body.source)).all()
    renamed = skipped = 0
    for t in rows:
        try:
            assert_editable(session, t)
        except Exception:
            skipped += 1
            continue
        t.payee = body.target
        renamed += 1
    session.flush()
    emit(session, household.id, actor, "rename", "payee", 0,
         {"from": body.source, "to": body.target,
          "renamed": renamed, "skipped": skipped})
    return schemas.PayeeRenameOut(renamed=renamed, skipped=skipped)
