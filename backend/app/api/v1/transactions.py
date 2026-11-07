"""Transactions: filtered/paginated list, create, gated patch, bulk ops."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain.periods import assert_editable
from app.domain.periods import transition as apply_transition
from app.persistence.models import Account, Transaction

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _get(
    session: Session, household_id: int, txn_id: int
) -> Transaction:
    t = session.get(Transaction, txn_id)
    if t is None:
        raise HTTPException(404, f"transaction {txn_id} not found")
    a = session.get(Account, t.account_id)
    if a is None or a.household_id != household_id:
        raise HTTPException(404, f"transaction {txn_id} not found")
    return t


@router.get("", response_model=schemas.Page[schemas.TransactionOut])
def list_transactions(
    household: HouseholdDep, session: SessionDep,
    account_id: Annotated[int | None, Query()] = None,
    category_id: Annotated[int | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> schemas.Page[schemas.TransactionOut]:
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household.id)
    )
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if category_id is not None:
        stmt = stmt.where(Transaction.category_id == category_id)
    if status is not None:
        stmt = stmt.where(Transaction.status == status)
    if date_from is not None:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Transaction.date <= date_to)
    if q:
        stmt = stmt.where(Transaction.payee.ilike(f"%{q}%"))
    total = int(session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one())
    rows = session.execute(
        stmt.order_by(Transaction.date.desc(), Transaction.id.desc())
        .offset((page - 1) * size).limit(size)
    ).scalars().all()
    return schemas.Page(
        items=[schemas.TransactionOut.model_validate(t) for t in rows],
        total=total, page=page, size=size)


@router.post("", response_model=schemas.TransactionOut, status_code=201)
def create_transaction(
    body: schemas.TransactionIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.TransactionOut:
    a = session.get(Account, body.account_id)
    if a is None or a.household_id != household.id:
        raise HTTPException(404, f"account {body.account_id} not found")
    t = Transaction(
        account_id=a.id, date=body.date,
        amount_pence=body.amount.pence, payee=body.payee,
        category_id=body.category_id, notes=body.notes,
        external_ref=body.external_ref,
    )
    session.add(t)
    session.flush()
    if body.refund_of_id is not None:
        from app.domain.refunds import link_refund
        orig = _get(session, household.id, body.refund_of_id)
        link_refund(session, t, orig)
    emit(session, household.id, actor, "create", "transaction", t.id,
         {"account_id": a.id, "amount_pence": t.amount_pence})
    return schemas.TransactionOut.model_validate(t)


@router.get("/{txn_id}", response_model=schemas.TransactionOut)
def get_transaction(
    txn_id: int, household: HouseholdDep, session: SessionDep,
) -> schemas.TransactionOut:
    return schemas.TransactionOut.model_validate(
        _get(session, household.id, txn_id))


@router.patch("/{txn_id}", response_model=schemas.TransactionOut)
def patch_transaction(
    txn_id: int, body: schemas.TransactionPatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.TransactionOut:
    t = _get(session, household.id, txn_id)
    assert_editable(session, t)
    changed = body.model_dump(exclude_unset=True)
    new_status = changed.pop("status", None)
    for k, v in changed.items():
        setattr(t, k, v)
    if new_status is not None:
        apply_transition(session, t, new_status)
    session.flush()
    emit(session, household.id, actor, "update", "transaction",
         t.id, changed | ({"status": new_status} if new_status else {}))
    return schemas.TransactionOut.model_validate(t)


@router.post("/bulk", response_model=schemas.BulkResult)
def bulk_transactions(
    body: schemas.BulkTransactionOp, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.BulkResult:
    """Categorise / status-change many rows; per-row failures reported."""
    applied: list[int] = []
    rejected: list[dict[str, str]] = []
    for txn_id in body.ids:
        try:
            t = _get(session, household.id, txn_id)
            assert_editable(session, t)
            if body.category_id is not None:
                t.category_id = body.category_id
            if body.status is not None:
                apply_transition(session, t, body.status)
            applied.append(t.id)
        except HTTPException:
            rejected.append({"id": str(txn_id), "error": "not found"})
        except Exception as exc:  # domain rule → per-row rejection
            rejected.append({"id": str(txn_id), "error": str(exc)})
    session.flush()
    for tid in applied:
        emit(session, household.id, actor, "bulk_update",
             "transaction", tid)
    return schemas.BulkResult(applied=applied, rejected=rejected)
