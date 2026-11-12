"""Admin: manual backup / restore (issue #24).

Gated by ``X-Admin-Token`` (fail-closed when ``FF_ADMIN_TOKEN`` is
unset). Restore never replaces the live file — it verifies the snapshot
and writes the pending-restore marker the next boot applies.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.api import schemas
from app.api.audit import emit
from app.api.deps import (
    ActorDep,
    HouseholdDep,
    SessionDep,
    require_admin,
)
from app.core.settings import Settings
from app.domain import financial_events, outbox
from app.persistence import backup

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])


def _settings(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


@router.get("/backups", response_model=list[schemas.BackupOut])
def list_backups(request: Request) -> list[schemas.BackupOut]:
    backup_dir = _settings(request).resolved_backup_dir
    snaps = list(reversed(backup.list_snapshots(backup_dir)))
    return [
        schemas.BackupOut(name=p.name, bytes=p.stat().st_size,
                          ok=backup.verify_snapshot(p),
                          taken_at=backup.snapshot_taken_at(p))
        for p in snaps  # newest first (issue #371)
    ]


@router.post("/backups", response_model=schemas.BackupOut,
             status_code=201)
def create_backup(request: Request, household: HouseholdDep,
                  session: SessionDep, actor: ActorDep,
                  ) -> schemas.BackupOut:
    settings = _settings(request)
    try:
        snap = backup.create_snapshot(request.app.state.engine,
                                      settings.resolved_backup_dir)
        if not backup.verify_snapshot(snap):
            raise RuntimeError("backup integrity verification failed")
        deleted = backup.rotate(settings.resolved_backup_dir, settings.backup_keep)
    except Exception:
        # The failed operation has no financial mutation to commit. Persist a
        # sanitised ops transition separately so request rollback cannot erase it.
        session.rollback()
        financial_events.record_backup_status(session, household.id, succeeded=False)
        session.commit()
        raise
    financial_events.record_backup_status(session, household.id, succeeded=True)
    emit(session, household.id, actor, "backup", "database", 0,
         {"snapshot": snap.name, "rotated": deleted})
    outbox.emit(session, household.id, "backup.created", entity_kind="backup",
                payload={"status": "completed"}, dedup_key=f"backup:{snap.name}")
    return schemas.BackupOut(name=snap.name, bytes=snap.stat().st_size,
                             ok=True,
                             taken_at=backup.snapshot_taken_at(snap))


@router.get("/backups/{name}/download")
def download_backup(name: str, request: Request) -> FileResponse:
    """Stream a snapshot — moving installs is download + upload
    (issue #328). Name is pinned to the snapshot pattern, so no
    path traversal is possible."""
    if not backup.SNAPSHOT_RE.fullmatch(name):
        raise HTTPException(404, "snapshot not found")
    path = _settings(request).resolved_backup_dir / name
    if not path.is_file():
        raise HTTPException(404, "snapshot not found")
    return FileResponse(path, filename=name,
                        media_type="application/octet-stream")


@router.post("/restore/upload", response_model=schemas.RestoreAccepted,
             status_code=202)
def upload_restore(file: UploadFile, request: Request,
                   household: HouseholdDep, session: SessionDep,
                   actor: ActorDep) -> schemas.RestoreAccepted:
    """Accept a snapshot file, verify integrity, and queue the restore
    for next boot (issue #328)."""
    settings = _settings(request)
    # Uploads live under ``pending/`` — ``list_snapshots`` only scans
    # the backup dir itself, so a restore payload can never be
    # mistaken for a real backup (issue #342).
    pending_dir = settings.resolved_backup_dir / backup.PENDING_DIR
    pending_dir.mkdir(parents=True, exist_ok=True)
    seq = 0
    while (dest := pending_dir / backup.next_name(seq=seq)).exists():
        seq += 1
    dest.write_bytes(file.file.read())
    if not backup.verify_snapshot(dest):
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "file is not a valid snapshot")
    backup.request_restore(
        settings.restore_marker, f"{backup.PENDING_DIR}/{dest.name}")
    emit(session, household.id, actor, "restore_uploaded", "database",
         0, {"snapshot": dest.name})
    return schemas.RestoreAccepted(name=dest.name)


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
