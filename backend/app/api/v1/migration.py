"""Migration: reconciliation report, review queue, retirement gate
(issue #49). Flag resolutions are audited mutations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.migration.report import reconciliation_report, retirement_gate
from app.persistence.models import ImportBatch, MigrationFlag

router = APIRouter(prefix="/migration", tags=["migration"])

RESOLVABLE = ("resolved", "wont_fix", "deferred")


class FlagOut(BaseModel):
    id: int
    batch_id: int
    defect_id: str
    status: str
    detail: str | None
    reviewer_note: str | None


class ResolveIn(BaseModel):
    status: str = Field(pattern="^(resolved|wont_fix|deferred)$")
    note: str = Field(min_length=1, max_length=2000)


def _batch(session: Session, household_id: int,
           batch_id: int) -> ImportBatch:
    b: ImportBatch | None = session.get(ImportBatch, batch_id)
    if b is None or b.household_id != household_id:
        raise HTTPException(404, f"import batch {batch_id} not found")
    return b


@router.get("/batches/{batch_id}/report")
def get_report(household: HouseholdDep, session: SessionDep,
               batch_id: int) -> dict[str, Any]:
    """Structural reconciliation report — counts/residuals only."""
    return reconciliation_report(
        session, _batch(session, household.id, batch_id))


@router.get("/batches/{batch_id}/retirement-gate")
def get_retirement_gate(household: HouseholdDep, session: SessionDep,
                        batch_id: int) -> dict[str, Any]:
    """Workbook-retirement gate: ready only when committed and every
    flag resolved (#56 reads this)."""
    return retirement_gate(
        session, _batch(session, household.id, batch_id))


@router.get("/flags", response_model=list[FlagOut])
def list_flags(household: HouseholdDep, session: SessionDep,
               batch_id: int | None = None,
               unresolved_only: bool = False) -> list[FlagOut]:
    stmt = (select(MigrationFlag)
            .join(ImportBatch, MigrationFlag.batch_id == ImportBatch.id)
            .where(ImportBatch.household_id == household.id))
    if batch_id is not None:
        stmt = stmt.where(MigrationFlag.batch_id == batch_id)
    rows = list(session.execute(stmt).scalars().all())
    if unresolved_only:
        rows = [f for f in rows if f.status in ("detected",
                                               "acknowledged")]
    return [FlagOut(id=f.id, batch_id=f.batch_id,
                    defect_id=f.defect_id, status=f.status,
                    detail=f.detail, reviewer_note=f.reviewer_note)
            for f in rows]


@router.post("/flags/{flag_id}/resolve", response_model=FlagOut)
def resolve_flag(household: HouseholdDep, session: SessionDep,
                 actor: ActorDep, flag_id: int,
                 body: ResolveIn) -> FlagOut:
    flag = session.get(MigrationFlag, flag_id)
    if flag is None:
        raise HTTPException(404, f"flag {flag_id} not found")
    batch = session.get(ImportBatch, flag.batch_id)
    if batch is None or batch.household_id != household.id:
        raise HTTPException(404, f"flag {flag_id} not found")
    flag.status = body.status
    flag.reviewer_note = body.note
    emit(session, household.id, actor, "migration_flag.resolve",
         "migration_flag", flag.id,
         {"defect_id": flag.defect_id, "status": body.status})
    session.commit()
    return FlagOut(id=flag.id, batch_id=flag.batch_id,
                   defect_id=flag.defect_id, status=flag.status,
                   detail=flag.detail,
                   reviewer_note=flag.reviewer_note)
