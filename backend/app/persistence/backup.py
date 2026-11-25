"""Snapshot backup / restore / rotation (issue #24).

Snapshots are ``VACUUM INTO`` files under ``<data_dir>/backups`` —
consistent online copies safe to take on a live WAL database. Restore
while running is unsafe, so a restore request writes a marker file the
next boot applies *before* opening the engine or running migrations.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

from app.persistence import models  # noqa: F401 - register application tables
from app.persistence.base import Base
from app.persistence.engine import vacuum_into
from app.persistence.migrate import alembic_config, upgrade_head

SNAPSHOT_RE = re.compile(r"ff-\d{8}T\d{6}(-\d+)?\.db")
RESTORE_MARKER = "restore.pending"
PENDING_DIR = "pending"
# Restore markers may reference an uploaded file under ``pending/``
# (kept out of ``list_snapshots`` — issue #342) or a listed snapshot.
MARKER_RE = re.compile(
    rf"(?:{PENDING_DIR}/)?ff-\d{{8}}T\d{{6}}(-\d+)?\.db")


def next_name(now: datetime | None = None, seq: int = 0) -> str:
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    stamp = instant.astimezone(UTC).strftime("%Y%m%dT%H%M%S")
    suffix = f"-{seq}" if seq else ""
    return f"ff-{stamp}{suffix}.db"


def create_snapshot(engine: Engine, backup_dir: Path,
                    now: datetime | None = None) -> Path:
    """Timestamped online snapshot; filename seq-suffixed on collision."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    seq = 0
    while True:
        target = backup_dir / next_name(now, seq)
        if not target.exists():
            break
        seq += 1
    vacuum_into(engine, target)
    taken_at = now or datetime.now(UTC)
    if taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=UTC)
    # Embedded in the snapshot itself: downloads, copies and restores
    # preserve provenance independently of filename and filesystem mtime.
    with closing(sqlite3.connect(target)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ff_backup_manifest (taken_at TEXT NOT NULL)")
        conn.execute("DELETE FROM ff_backup_manifest")
        conn.execute("INSERT INTO ff_backup_manifest VALUES (?)",
                     (taken_at.astimezone(UTC).isoformat(),))
    return target


def snapshot_taken_at(path: Path) -> datetime | None:
    """Read embedded UTC provenance, falling back to legacy filenames."""
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT taken_at FROM ff_backup_manifest LIMIT 1").fetchone()
        if row is not None:
            stamp = datetime.fromisoformat(row[0])
            if stamp.tzinfo is not None:
                return stamp.astimezone(UTC)
    except (sqlite3.Error, ValueError, TypeError):
        pass
    match = re.fullmatch(r"ff-(\d{8}T\d{6})(?:-\d+)?\.db", path.name)
    if match is not None:
        try:
            return datetime.strptime(match[1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def list_snapshots(backup_dir: Path) -> list[Path]:
    if not backup_dir.is_dir():
        return []
    def key(path: Path) -> tuple[datetime, int, str]:
        suffix = re.fullmatch(r"ff-\d{8}T\d{6}(?:-(\d+))?\.db", path.name)
        seq = int(suffix[1] or 0) if suffix else 0
        return (snapshot_taken_at(path) or datetime.min.replace(tzinfo=UTC), seq, path.name)

    return sorted((p for p in backup_dir.iterdir() if SNAPSHOT_RE.fullmatch(p.name)), key=key)


def latest_snapshot(backup_dir: Path) -> Path | None:
    snaps = list_snapshots(backup_dir)
    return snaps[-1] if snaps else None


def rotate(backup_dir: Path, keep: int) -> int:
    """Keep the newest ``keep`` snapshots; returns the deleted count."""
    snaps = list_snapshots(backup_dir)
    doomed = snaps[:-keep] if keep > 0 else snaps
    for p in doomed:
        p.unlink(missing_ok=True)
    return len(doomed)


@cache
def _supported_revisions() -> frozenset[str]:
    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    return frozenset(revision.revision for revision in scripts.walk_revisions())


def verify_snapshot(path: Path) -> bool:
    """Check file integrity, Family Finance identity and supported migration history."""
    if not path.is_file() or not SNAPSHOT_RE.fullmatch(path.name):
        return False
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                return False
            conn.execute("SELECT key, value FROM app_meta LIMIT 0")
            revisions = {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}
            return bool(revisions) and revisions <= _supported_revisions()
    except sqlite3.Error:
        return False


def restore_from(snapshot: Path, db_path: Path, *, migrate: bool = False) -> None:
    """Prepare an isolated copy, then replace the destination in one SQLite transaction."""
    with TemporaryDirectory(dir=db_path.parent) as directory:
        staged = Path(directory) / snapshot.name
        shutil.copyfile(snapshot, staged)
        if migrate:
            if not verify_snapshot(staged):
                raise ValueError("Incompatible or damaged Family Finance snapshot")
            upgrade_head(f"sqlite:///{staged.as_posix()}")
        with closing(sqlite3.connect(staged)) as source:
            if source.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Damaged Family Finance snapshot")
            if migrate:
                # A stamped revision alone does not prove the app can use these tables.
                columns = {
                    (table, column) for table, column in source.execute(
                        "SELECT m.name, p.name FROM sqlite_master m, pragma_table_info(m.name) p "
                        "WHERE m.type='table'"
                    )
                }
                if any((table.name, column.name) not in columns
                       for table in Base.metadata.tables.values() for column in table.columns):
                    raise ValueError("Restored database is missing required application columns")
                if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("Restored database contains invalid references")
            # SQLite owns the destination journal and rolls back on write failure.
            with closing(sqlite3.connect(db_path)) as destination:
                source.backup(destination)


def request_restore(marker: Path, snapshot_name: str) -> None:
    """Write the pending-restore marker — applied at next boot."""
    if not MARKER_RE.fullmatch(snapshot_name):
        raise ValueError(f"invalid snapshot name: {snapshot_name!r}")
    marker.write_text(snapshot_name)


def take_pending_restore(marker: Path) -> str | None:
    """Return the requested snapshot name and clear the marker."""
    if not marker.is_file():
        return None
    name = marker.read_text().strip()
    marker.unlink()
    return name if MARKER_RE.fullmatch(name) else None
