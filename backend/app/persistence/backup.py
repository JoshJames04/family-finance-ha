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
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import Engine

from app.persistence.engine import vacuum_into

SNAPSHOT_RE = re.compile(r"ff-\d{8}T\d{6}(-\d+)?\.db")
RESTORE_MARKER = "restore.pending"
PENDING_DIR = "pending"
# Restore markers may reference an uploaded file under ``pending/``
# (kept out of ``list_snapshots`` — issue #342) or a listed snapshot.
MARKER_RE = re.compile(
    rf"(?:{PENDING_DIR}/)?ff-\d{{8}}T\d{{6}}(-\d+)?\.db")


def next_name(now: datetime | None = None, seq: int = 0) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S")
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
    with sqlite3.connect(target) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ff_backup_manifest (taken_at TEXT NOT NULL)")
        conn.execute("DELETE FROM ff_backup_manifest")
        conn.execute("INSERT INTO ff_backup_manifest VALUES (?)",
                     (taken_at.astimezone(UTC).isoformat(),))
    return target


def snapshot_taken_at(path: Path) -> datetime | None:
    """Read embedded UTC provenance, falling back to legacy filenames."""
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
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
    return sorted(p for p in backup_dir.iterdir()
                  if SNAPSHOT_RE.fullmatch(p.name))


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


def verify_snapshot(path: Path) -> bool:
    """True when the snapshot opens and passes integrity_check."""
    if not path.is_file() or not SNAPSHOT_RE.fullmatch(path.name):
        return False
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro",
                               uri=True)
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error:
        return False
    finally:
        if conn is not None:
            conn.close()
    return len(rows) == 1 and rows[0][0] == "ok"


def restore_from(snapshot: Path, db_path: Path) -> None:
    """Replace ``db_path`` with ``snapshot`` (engine must be disposed).

    Stale WAL/SHM sidecars are removed first so no pre-restore journal
    can replay over the restored file."""
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    shutil.copyfile(snapshot, db_path)


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
