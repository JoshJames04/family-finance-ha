"""Admin: manual backup / restore (issue #24).

Gated by ``X-Admin-Token`` (fail-closed when ``FF_ADMIN_TOKEN`` is
unset). Restore never replaces the live file — it verifies the snapshot
and writes the pending-restore marker the next boot applies.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import schemas
from app.api.audit import emit
from app.api.deps import (
    ActorDep,
    HouseholdDep,
    SessionDep,
    require_admin,
)
from app.core.settings import Settings
from app.persistence import backup

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])


def _settings(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


@router.get("/backups", response_model=list[schemas.BackupOut])
def list_backups(request: Request) -> list[schemas.BackupOut]:
    backup_dir = _settings(request).resolved_backup_dir
    return [
        schemas.BackupOut(name=p.name, bytes=p.stat().st_size,
                          ok=backup.verify_snapshot(p))
        for p in backup.list_snapshots(backup_dir)
    ]


@router.post("/backups", response_model=schemas.BackupOut,
             status_code=201)
def create_backup(request: Request, household: HouseholdDep,
                  session: SessionDep, actor: ActorDep,
                  ) -> schemas.BackupOut:
    settings = _settings(request)
    snap = backup.create_snapshot(request.app.state.engine,
                                  settings.resolved_backup_dir)
    deleted = backup.rotate(settings.resolved_backup_dir,
                            settings.backup_keep)
    emit(session, household.id, actor, "backup", "database", 0,
         {"snapshot": snap.name, "rotated": deleted})
    return schemas.BackupOut(name=snap.name, bytes=snap.stat().st_size,
                             ok=backup.verify_snapshot(snap))


@router.post("/restore", response_model=schemas.RestoreAccepted,
             status_code=202)
def request_restore(body: schemas.RestoreRequest, request: Request,
                    household: HouseholdDep, session: SessionDep,
                    actor: ActorDep) -> schemas.RestoreAccepted:
    settings = _settings(request)
    snap = settings.resolved_backup_dir / body.name
    if not backup.verify_snapshot(snap):
        raise HTTPException(404, "snapshot missing or failed integrity")
    backup.request_restore(settings.restore_marker, snap.name)
    emit(session, household.id, actor, "restore_requested", "database",
         0, {"snapshot": snap.name})
    return schemas.RestoreAccepted(name=snap.name)
