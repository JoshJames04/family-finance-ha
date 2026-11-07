"""SQLite engine with production pragmas per ADR-0001.

WAL + busy_timeout allow concurrent readers during writes; foreign_keys ON
enforces integrity; synchronous=NORMAL is safe under WAL. Single-writer
discipline is enforced by only ever writing through this engine.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA foreign_keys=ON",
    "PRAGMA synchronous=NORMAL",
)


def create_app_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        db_path = url.removeprefix("sqlite:///")
        if db_path not in (":memory:", ""):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 5}
        if url.startswith("sqlite") else {},
        pool_pre_ping=True,
    )

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_pragmas(dbapi_conn: sqlite3.Connection,
                         _record: object) -> None:
            cur = dbapi_conn.cursor()
            for pragma in PRAGMAS:
                cur.execute(pragma)
            cur.close()

    return engine


def integrity_check(engine: Engine) -> bool:
    """Run SQLite integrity_check; returns True when clean."""
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA integrity_check")).fetchall()
    return len(rows) == 1 and rows[0][0] == "ok"


def vacuum_into(engine: Engine, target: str | Path) -> Path:
    """Online backup via VACUUM INTO — safe on a live DB."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with engine.connect() as conn:
        conn.execute(text(f"VACUUM INTO '{target.as_posix()}'"))
    return target
