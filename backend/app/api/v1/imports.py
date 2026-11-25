"""Statement imports (issue #288): upload → preview → commit/rollback.

The preview persists a ``statement_import`` row holding the normalized
payload — what the member reviews is what commit inserts. Commit and
rollback are audit-evented; fingerprints dedup re-imports at the
transaction constraint."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.api.v1.accounts import _get as get_account
from app.api.v1.accounts import _write as require_account_write
from app.domain import imports, rbac
from app.persistence.models import (
    Account,
    StatementImport,
    StatementImportStatus,
    UserProfile,
)

router = APIRouter(prefix="/imports", tags=["imports"])

_FORMATS = {"csv", "ofx", "qif"}


def _batch(session: Session, household_id: int,
           import_id: int, profile: UserProfile | None) -> StatementImport:
    b = session.get(StatementImport, import_id)
    if b is None or b.household_id != household_id:
        raise HTTPException(404, "import not found")
    require_account_write(get_account(session, household_id, b.account_id, profile), profile)
    return b


def _out(b: StatementImport) -> schemas.StatementImportOut:
    return schemas.StatementImportOut(
        id=b.id, account_id=b.account_id, filename=b.filename,
        format=b.format, status=b.status.value,
        rows_total=b.rows_total, rows_imported=b.rows_imported,
        rows_skipped=b.rows_skipped, created_at=b.created_at)


@router.post("/preview", response_model=schemas.StatementImportPreviewOut,
             status_code=201)
async def preview(
    file: UploadFile, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
    account_id: int = Form(), fmt: str = Form("csv"),
    mapping: str = Form("{}"),
) -> schemas.StatementImportPreviewOut:
    require_account_write(get_account(session, household.id, account_id, profile), profile)
    if fmt not in _FORMATS:
        raise HTTPException(422, f"unsupported format {fmt!r}")
    try:
        map_obj = json.loads(mapping)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "mapping is not valid JSON") from exc
    content = await file.read()
    try:
        rows = imports.parse(content, fmt, map_obj)
        imports.mark_duplicates(session, account_id, rows)
    except imports.ImportError_ as exc:
        raise HTTPException(422, str(exc)) from exc
    batch = StatementImport(
        household_id=household.id, account_id=account_id,
        filename=(file.filename or "statement")[:255], format=fmt,
        mapping=map_obj,
        payload={"rows": [{"date": r.date.isoformat(),
                           "amount_pence": r.amount_pence,
                           "payee": r.payee, "notes": r.notes, "bank_ref": r.bank_ref}
                          for r in rows]},
        status=StatementImportStatus.preview,
        rows_total=len(rows))
    session.add(batch)
    session.flush()
    emit(session, household.id, actor, "create", "statement_import",
         batch.id, {"filename": batch.filename, "rows": len(rows)})
    return schemas.StatementImportPreviewOut(
        import_row=_out(batch),
        rows=[schemas.StatementImportRowOut(
            date=r.date, amount_pence=r.amount_pence, payee=r.payee,
            notes=r.notes, duplicate=r.duplicate, bank_id_hint=r.bank_id_hint,
            legacy_transaction_id=r.legacy_transaction_id) for r in rows])


@router.post("/{import_id}/commit",
             response_model=schemas.StatementImportOut)
def commit_import(
    import_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
    body: schemas.StatementImportChoices | None = None,
) -> schemas.StatementImportOut:
    b = _batch(session, household.id, import_id, profile)
    if b.status != StatementImportStatus.preview:
        raise HTTPException(
            409, f"import is {b.status.value}, cannot commit")
    try:
        inserted, skipped = imports.commit(session, b, body.legacy_matches if body else None)
    except imports.ImportError_ as exc:
        raise HTTPException(409, str(exc)) from exc
    b.status = StatementImportStatus.committed
    b.rows_imported, b.rows_skipped = inserted, skipped
    b.committed_at = datetime.now(UTC)
    session.flush()
    emit(session, household.id, actor, "update", "statement_import",
         b.id, {"status": "committed", "imported": inserted,
                "skipped": skipped})
    return _out(b)


@router.post("/{import_id}/rollback",
             response_model=schemas.StatementImportOut)
def rollback_import(
    import_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.StatementImportOut:
    b = _batch(session, household.id, import_id, profile)
    if b.status != StatementImportStatus.committed:
        raise HTTPException(
            409, "only committed imports can be rolled back")
    try:
        removed = imports.rollback(session, b)
    except imports.ImportError_ as exc:
        raise HTTPException(409, str(exc)) from exc
    b.status = StatementImportStatus.rolled_back
    b.rolled_back_at = datetime.now(UTC)
    session.flush()
    emit(session, household.id, actor, "update", "statement_import",
         b.id, {"status": "rolled_back", "removed": removed})
    return _out(b)


@router.get("", response_model=schemas.Page[schemas.StatementImportOut])
def list_imports(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    account_id: int | None = None,
) -> schemas.Page[schemas.StatementImportOut]:
    q = (select(StatementImport, Account.owner_profile_id).join(
        Account, Account.id == StatementImport.account_id)
         .where(StatementImport.household_id == household.id, Account.household_id == household.id)
         .order_by(StatementImport.id.desc()))
    if account_id is not None:
        q = q.where(StatementImport.account_id == account_id)
    rows = [b for b, owner in session.execute(q) if rbac.visible(owner, profile)]
    return schemas.Page(items=[_out(b) for b in rows],
                        total=len(rows), page=1, size=len(rows) or 1)
