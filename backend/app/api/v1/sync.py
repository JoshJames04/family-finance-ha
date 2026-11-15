"""Feed sync monitoring + merge review (issue #67). The ingest
engine lives in domain.sync and runs against caller-supplied feeds
(the Starling client wires in with Epic 6); these endpoints expose
cursor/run state and the merge-candidate review queue."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import rbac
from app.domain import sync as sync_domain
from app.domain.sync import SyncError
from app.persistence.models import (
    Account,
    MergeCandidate,
    MergeStatus,
    SyncCursor,
    SyncRun,
    Transaction,
    UserProfile,
)

router = APIRouter(prefix="/sync", tags=["sync"])


def _account(session: Session, household_id: int,
             account_id: int, profile: UserProfile | None = None) -> Account:
    a = session.get(Account, account_id)
    if (a is None or a.household_id != household_id
            or not rbac.visible(a.owner_profile_id, profile)):
        raise HTTPException(404, f"account {account_id} not found")
    return a


@router.get("/accounts/{account_id}/cursor",
            response_model=list[schemas.SyncCursorOut])
def cursor_state(
    account_id: int, household: HouseholdDep, session: SessionDep, profile: ProfileDep,
) -> list[schemas.SyncCursorOut]:
    """Resumable position + last error per feed source."""
    a = _account(session, household.id, account_id, profile)
    rows = session.scalars(
        select(SyncCursor)
        .where(SyncCursor.account_id == a.id)
        .order_by(SyncCursor.source))
    return [schemas.SyncCursorOut.model_validate(c) for c in rows]


@router.get("/accounts/{account_id}/runs",
            response_model=list[schemas.SyncRunOut])
def run_history(
    account_id: int, household: HouseholdDep, session: SessionDep, profile: ProfileDep,
) -> list[schemas.SyncRunOut]:
    a = _account(session, household.id, account_id, profile)
    rows = session.scalars(
        select(SyncRun)
        .where(SyncRun.account_id == a.id)
        .order_by(SyncRun.id.desc()).limit(50))
    return [schemas.SyncRunOut.model_validate(r) for r in rows]


@router.get("/merge-candidates",
            response_model=list[schemas.MergeCandidateOut])
def merge_candidates(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
) -> list[schemas.MergeCandidateOut]:
    rows = session.execute(
        select(MergeCandidate, Account.owner_profile_id)
        .join(Account, MergeCandidate.account_id == Account.id)
        .join(Transaction, MergeCandidate.existing_txn_id == Transaction.id)
        .where(MergeCandidate.household_id == household.id,
               Account.household_id == household.id,
               Transaction.account_id == Account.id,
               MergeCandidate.status == MergeStatus.pending)
        .order_by(MergeCandidate.id))
    return [schemas.MergeCandidateOut.model_validate(c) for c, owner in rows
            if rbac.visible(owner, profile)]


@router.post("/merge-candidates/{cid}/resolve",
             response_model=schemas.MergeCandidateOut)
def resolve_candidate(
    cid: int, body: schemas.MergeResolveIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.MergeCandidateOut:
    c = session.get(MergeCandidate, cid)
    if c is None or c.household_id != household.id:
        raise HTTPException(404, f"candidate {cid} not found")
    account = _account(session, household.id, c.account_id, profile)
    if not rbac.write_allowed(account.owner_profile_id, profile):
        raise HTTPException(403, "role cannot mutate this scope")
    txn = session.get(Transaction, c.existing_txn_id)
    if txn is None or txn.account_id != account.id:
        raise HTTPException(404, f"candidate {cid} not found")
    try:
        sync_domain.resolve_merge(session, c, body.action)
    except SyncError as e:
        raise HTTPException(422, str(e)) from e
    emit(session, household.id, actor, body.action,
         "merge_candidate", c.id)
    return schemas.MergeCandidateOut.model_validate(c)
