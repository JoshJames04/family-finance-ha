"""Reconciliation review queue (issue #69): run matching, list
pending matches, confirm or dismiss them."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import reconcile as recon_domain
from app.domain.reconcile import ReconError
from app.persistence.models import (
    Account,
    Reconciliation,
    ReconStatus,
)

router = APIRouter(prefix="/reconcile", tags=["reconcile"])


def _account(session: Session, household_id: int,
             account_id: int) -> Account:
    a = session.get(Account, account_id)
    if a is None or a.household_id != household_id:
        raise HTTPException(404, f"account {account_id} not found")
    return a


def _row(session: Session, household_id: int,
         rid: int) -> Reconciliation:
    r = session.get(Reconciliation, rid)
    if r is None or r.household_id != household_id:
        raise HTTPException(404, f"reconciliation {rid} not found")
    return r


@router.post("/accounts/{account_id}/run",
             response_model=schemas.ReconStatsOut)
def run(account_id: int, household: HouseholdDep,
        session: SessionDep, actor: ActorDep) -> schemas.ReconStatsOut:
    """Re-score pending transactions against expected movements."""
    a = _account(session, household.id, account_id)
    stats = recon_domain.reconcile_account(session, a, date.today())
    emit(session, household.id, actor, "reconcile_run",
         "account", a.id)
    return schemas.ReconStatsOut(**stats.__dict__)


@router.get("/queue",
            response_model=list[schemas.ReconciliationOut])
def queue(household: HouseholdDep,
          session: SessionDep) -> list[schemas.ReconciliationOut]:
    rows = session.scalars(
        select(Reconciliation)
        .where(Reconciliation.household_id == household.id,
               Reconciliation.status == ReconStatus.pending)
        .order_by(Reconciliation.score.desc()))
    return [schemas.ReconciliationOut.model_validate(r)
            for r in rows]


@router.post("/{rid}/confirm",
             response_model=schemas.ReconciliationOut)
def confirm(rid: int, household: HouseholdDep,
            session: SessionDep,
            actor: ActorDep) -> schemas.ReconciliationOut:
    r = _row(session, household.id, rid)
    try:
        recon_domain.confirm(session, r)
    except ReconError as e:
        raise HTTPException(422, str(e)) from e
    emit(session, household.id, actor, "reconcile_confirm",
         "reconciliation", r.id)
    return schemas.ReconciliationOut.model_validate(r)


@router.post("/{rid}/dismiss",
             response_model=schemas.ReconciliationOut)
def dismiss(rid: int, household: HouseholdDep,
            session: SessionDep,
            actor: ActorDep) -> schemas.ReconciliationOut:
    r = _row(session, household.id, rid)
    try:
        recon_domain.dismiss(session, r)
    except ReconError as e:
        raise HTTPException(422, str(e)) from e
    emit(session, household.id, actor, "reconcile_dismiss",
         "reconciliation", r.id)
    return schemas.ReconciliationOut.model_validate(r)
