"""Transactions: filtered/paginated list, create, gated patch, bulk ops."""

import csv
import io
import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import financial_events, rbac, splits
from app.domain import transactions as txn_domain
from app.domain.money import Money
from app.domain.periods import assert_editable
from app.domain.periods import transition as apply_transition
from app.domain.splits import SplitError
from app.domain.transactions import CorrectionError
from app.persistence.models import (
    Account,
    Category,
    Role,
    Transaction,
    TransactionSplit,
    TransactionStatus,
    UserProfile,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _get(
    session: Session, household_id: int, txn_id: int,
    profile: UserProfile | None = None,
) -> Transaction:
    t = session.get(Transaction, txn_id)
    if t is None:
        raise HTTPException(404, f"transaction {txn_id} not found")
    a = session.get(Account, t.account_id)
    if (a is None or a.household_id != household_id
            or not rbac.visible(a.owner_profile_id, profile)):
        raise HTTPException(404, f"transaction {txn_id} not found")
    return t


def _write_account(session: Session, household_id: int,
                   account_id: int,
                   profile: UserProfile | None) -> None:
    """Mutation gate on the owning account's scope (issue #32)."""
    a = session.get(Account, account_id)
    if (a is None or a.household_id != household_id
            or not rbac.visible(a.owner_profile_id, profile)):
        raise HTTPException(404, "account not found")
    if not rbac.write_allowed(a.owner_profile_id, profile):
        raise HTTPException(403, "role cannot mutate this scope")


@router.get("", response_model=schemas.Page[schemas.TransactionOut])
def list_transactions(
    household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
    account_id: Annotated[int | None, Query()] = None,
    category_id: Annotated[int | None, Query()] = None,
    owner_profile_id: Annotated[int | None, Query()] = None,
    exclude_void: bool = False,
    exclude_opening: bool = False,
    status: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    direction: Annotated[str | None, Query(
        pattern="^(income|expense)$")] = None,
    internal: Annotated[str | None, Query(
        pattern="^(only|exclude)$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> schemas.Page[schemas.TransactionOut]:
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household.id)
    )
    # Private-account rows are invisible to other members (issue
    # #32) — same scope filter as the export endpoint.
    if profile is None:
        stmt = stmt.where(Account.owner_profile_id.is_(None))
    elif profile.role != Role.admin:
        stmt = stmt.where(
            Account.owner_profile_id.is_(None)
            | (Account.owner_profile_id == profile.id))
    if owner_profile_id is not None:
        stmt = stmt.where(Account.owner_profile_id == owner_profile_id)
    if exclude_void:
        stmt = stmt.where(Transaction.status != TransactionStatus.void)
    if exclude_opening:
        stmt = stmt.where(
            ~((Transaction.source == "system") & Transaction.external_ref.like("open:%"))
            | Transaction.external_ref.is_(None))
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
        text = q.strip()
        categories = select(Category.id).where(Category.name.icontains(text, autoescape=True))
        matches = [Transaction.payee.icontains(text, autoescape=True),
                   Transaction.notes.icontains(text, autoescape=True),
                   Transaction.category_id.in_(categories),
                   Transaction.id.in_(select(TransactionSplit.transaction_id).where(
                       TransactionSplit.category_id.in_(categories)))]
        numeric = text.replace("£", "").replace(",", "")
        if re.fullmatch(r"[+-]?\d{1,12}(?:\.\d{1,2})?", numeric):
            pence = Money.of_pounds(numeric).pence
            matches.append(Transaction.amount_pence == pence if numeric.startswith(("-", "+"))
                           else func.abs(Transaction.amount_pence) == pence)
        stmt = stmt.where(or_(*matches))
    if direction == "income":
        stmt = stmt.where(Transaction.amount_pence > 0)
    elif direction == "expense":
        stmt = stmt.where(Transaction.amount_pence < 0)
    if internal == "only":
        stmt = stmt.where(Transaction.transfer_id.isnot(None))
    elif internal == "exclude":
        stmt = stmt.where(Transaction.transfer_id.is_(None))
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


_EXPORT_COLUMNS = [
    "id", "date", "settled_date", "account", "payee", "category",
    "amount_pence", "status", "source", "transfer_id", "notes",
]


@router.get("/export.csv", response_class=PlainTextResponse)
def export_transactions(
    household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
) -> PlainTextResponse:
    """RFC-4180 CSV export of the period (issue #115). Member-scoped:
    anonymous sees shared accounts only; a member sees shared + their
    own; admin sees everything. Ordered (date, id) — deterministic."""
    if date_to < date_from:
        raise HTTPException(422, "date_to before date_from")
    stmt = (
        select(Transaction, Account.name, Category.name)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(Account.household_id == household.id,
               Transaction.date >= date_from,
               Transaction.date <= date_to))
    if profile is None:
        stmt = stmt.where(Account.owner_profile_id.is_(None))
    elif profile.role != Role.admin:
        stmt = stmt.where(or_(
            Account.owner_profile_id.is_(None),
            Account.owner_profile_id == profile.id))
    rows = session.execute(
        stmt.order_by(Transaction.date, Transaction.id)).all()

    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(_EXPORT_COLUMNS)
    for t, account_name, category_name in rows:
        w.writerow([
            t.id, t.date.isoformat(),
            t.settled_date.isoformat() if t.settled_date else "",
            account_name, t.payee, category_name or "",
            t.amount_pence, t.status.value, t.source.value,
            t.transfer_id or "", t.notes or "",
        ])
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="transactions_'
                 f"{date_from}_{date_to}.csv\""})


@router.post("", response_model=schemas.TransactionOut, status_code=201)
def create_transaction(
    body: schemas.TransactionIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.TransactionOut:
    _write_account(session, household.id, body.account_id, profile)
    financial_events.evaluate_budgets(session, household.id)
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
        orig = _get(session, household.id, body.refund_of_id,
                    profile)
        link_refund(session, t, orig)
    emit(session, household.id, actor, "create", "transaction", t.id,
         {"account_id": a.id, "amount_pence": t.amount_pence})
    financial_events.evaluate_budgets(session, household.id)
    return schemas.TransactionOut.model_validate(t)


@router.get("/{txn_id}", response_model=schemas.TransactionOut)
def get_transaction(
    txn_id: int, household: HouseholdDep, session: SessionDep,
    profile: ProfileDep,
) -> schemas.TransactionOut:
    return schemas.TransactionOut.model_validate(
        _get(session, household.id, txn_id, profile))


@router.patch("/{txn_id}", response_model=schemas.TransactionOut)
def patch_transaction(
    txn_id: int, body: schemas.TransactionPatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.TransactionOut:
    t = _get(session, household.id, txn_id, profile)
    _write_account(session, household.id, t.account_id, profile)
    financial_events.evaluate_budgets(session, household.id)
    assert_editable(session, t)
    changed = body.model_dump(exclude_unset=True)
    new_status = changed.pop("status", None)
    for k, v in changed.items():
        setattr(t, k, v)
    if "category_id" in changed and t.category_id is not None:
        from app.domain import categorise
        t.suggested_category_id = None
        categorise.learn(session, household.id, t.payee,
                         t.category_id, account_id=t.account_id)
    if new_status is not None:
        apply_transition(session, t, new_status)
    session.flush()
    emit(session, household.id, actor, "update", "transaction",
         t.id, changed | ({"status": new_status} if new_status else {}))
    financial_events.evaluate_budgets(session, household.id)
    return schemas.TransactionOut.model_validate(t)


@router.put("/{txn_id}/splits", response_model=schemas.TransactionOut)
def put_splits(
    txn_id: int, body: schemas.TransactionSplitsPut,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.TransactionOut:
    """Replace a transaction's split set (issue #289). Splits must sum
    to the parent amount; reconciled rows refuse — use ``correct``."""
    t = _get(session, household.id, txn_id, profile)
    _write_account(session, household.id, t.account_id, profile)
    financial_events.evaluate_budgets(session, household.id)
    try:
        splits.replace(session, t, household.id,
                       [s.model_dump() for s in body.splits])
    except SplitError as e:
        raise HTTPException(422, str(e)) from e
    session.flush()
    emit(session, household.id, actor, "update", "transaction",
         t.id, {"splits": len(body.splits)})
    financial_events.evaluate_budgets(session, household.id)
    return schemas.TransactionOut.model_validate(t)


@router.post("/{txn_id}/correct", response_model=schemas.TransactionOut)
def correct_transaction(
    txn_id: int, body: schemas.TransactionCorrect,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
    profile: ProfileDep,
) -> schemas.TransactionOut:
    """Supersede a reconciled transaction with a corrected pending row
    (issue #124). Reason is required — corrections are audited, the
    original is voided, never edited."""
    t = _get(session, household.id, txn_id, profile)
    _write_account(session, household.id, t.account_id, profile)
    financial_events.evaluate_budgets(session, household.id)
    try:
        corrected = txn_domain.correct(
            session, t, body.reason, payee=body.payee,
            category_id=body.category_id, notes=body.notes)
    except CorrectionError as e:
        raise HTTPException(422, str(e)) from e
    if body.category_id is not None:
        from app.domain import categorise
        categorise.learn(session, household.id, corrected.payee,
                         body.category_id, account_id=corrected.account_id)
    session.flush()
    emit(session, household.id, actor, "correct", "transaction",
         corrected.id, {"corrects": txn_id, "reason": body.reason})
    financial_events.evaluate_budgets(session, household.id)
    return schemas.TransactionOut.model_validate(corrected)


@router.post("/bulk", response_model=schemas.BulkResult)
def bulk_transactions(
    body: schemas.BulkTransactionOp, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.BulkResult:
    """Categorise / status-change many rows; per-row failures reported."""
    seeded = False
    applied: list[int] = []
    rejected: list[dict[str, str]] = []
    for txn_id in body.ids:
        try:
            t = _get(session, household.id, txn_id, profile)
            _write_account(session, household.id, t.account_id,
                           profile)
            assert_editable(session, t)
            if not seeded:
                financial_events.evaluate_budgets(session, household.id)
                seeded = True
            if body.category_id is not None:
                from app.domain import categorise
                t.category_id = body.category_id
                t.suggested_category_id = None
                categorise.learn(session, household.id, t.payee,
                                 body.category_id, account_id=t.account_id)
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
    if applied:
        financial_events.evaluate_budgets(session, household.id)
    return schemas.BulkResult(applied=applied, rejected=rejected)
