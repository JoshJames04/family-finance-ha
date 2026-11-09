"""Anomaly review queue (issue #114): run a scan, list pending
flags, confirm or dismiss — dismissals feed the suppression
thresholds."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import anomaly
from app.persistence.models import (
    Account,
    AnomalyFlag,
    AnomalyStatus,
)

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.post("/accounts/{account_id}/scan",
             response_model=schemas.AnomalyScanOut)
def scan(account_id: int, household: HouseholdDep,
         session: SessionDep,
         actor: ActorDep) -> schemas.AnomalyScanOut:
    a = session.get(Account, account_id)
    if a is None or a.household_id != household.id:
        raise HTTPException(404, f"account {account_id} not found")
    stats = anomaly.scan_account(session, a, date.today())
    emit(session, household.id, actor, "anomaly_scan",
         "account", a.id)
    return schemas.AnomalyScanOut(**stats.__dict__)


@router.get("/queue",
            response_model=list[schemas.AnomalyFlagOut])
def queue(household: HouseholdDep,
          session: SessionDep) -> list[schemas.AnomalyFlagOut]:
    rows = session.scalars(
        select(AnomalyFlag)
        .where(AnomalyFlag.household_id == household.id,
               AnomalyFlag.status == AnomalyStatus.pending)
        .order_by(AnomalyFlag.id))
    return [schemas.AnomalyFlagOut.model_validate(f) for f in rows]


def _flag(session: Session, household_id: int,
          fid: int) -> AnomalyFlag:
    f = session.get(AnomalyFlag, fid)
    if f is None or f.household_id != household_id:
        raise HTTPException(404, f"flag {fid} not found")
    if f.status != AnomalyStatus.pending:
        raise HTTPException(422, f"flag {fid} is not pending")
    return f


@router.post("/{fid}/confirm",
             response_model=schemas.AnomalyFlagOut)
def confirm(fid: int, household: HouseholdDep, session: SessionDep,
            actor: ActorDep) -> schemas.AnomalyFlagOut:
    f = _flag(session, household.id, fid)
    f.status = AnomalyStatus.confirmed
    f.resolved_at = datetime.now()
    session.flush()
    emit(session, household.id, actor, "anomaly_confirm",
         "anomaly_flag", f.id)
    return schemas.AnomalyFlagOut.model_validate(f)


@router.post("/{fid}/dismiss",
             response_model=schemas.AnomalyFlagOut)
def dismiss(fid: int, household: HouseholdDep, session: SessionDep,
            actor: ActorDep) -> schemas.AnomalyFlagOut:
    f = _flag(session, household.id, fid)
    f.status = AnomalyStatus.dismissed
    f.resolved_at = datetime.now()
    session.flush()
    emit(session, household.id, actor, "anomaly_dismiss",
         "anomaly_flag", f.id)
    return schemas.AnomalyFlagOut.model_validate(f)
