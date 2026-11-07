"""Persistence layer: pragmas, concurrency, migrations, backup."""

import sqlite3
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import text

from app.persistence.engine import (
    create_app_engine,
    integrity_check,
    vacuum_into,
)
from app.persistence.migrate import (
    alembic_config,
    current_revision,
    head_revision,
    upgrade_head,
)


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 't.db').as_posix()}"


def test_pragmas_applied(db_url: str) -> None:
    engine = create_app_engine(db_url)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_concurrent_reads_during_write(db_url: str) -> None:
    upgrade_head(db_url)
    engine = create_app_engine(db_url)
    errors: list[Exception] = []
    stop = threading.Event()

    def writer() -> None:
        try:
            for i in range(30):
                with engine.begin() as conn:
                    conn.execute(
                        text("INSERT INTO app_meta (key, value) VALUES (:k, :v)"),
                        {"k": f"k{i}", "v": str(i)},
                    )
                time.sleep(0.002)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def reader() -> None:
        try:
            while not stop.is_set():
                with engine.connect() as conn:
                    conn.execute(text("SELECT count(*) FROM app_meta")).scalar()
                time.sleep(0.001)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for r in readers:
        r.start()
    w = threading.Thread(target=writer)
    w.start()
    w.join(timeout=30)
    stop.set()
    for r in readers:
        r.join(timeout=30)
    assert errors == []


def test_migrations_upgrade_and_downgrade(db_url: str) -> None:
    from alembic import command

    cfg = alembic_config(db_url)
    upgrade_head(db_url)
    engine = create_app_engine(db_url)
    assert current_revision(engine) == head_revision(db_url)

    command.downgrade(cfg, "-1")
    engine2 = create_app_engine(db_url)
    assert current_revision(engine2) is None

    command.upgrade(cfg, "head")
    engine3 = create_app_engine(db_url)
    assert current_revision(engine3) == head_revision(db_url)


def test_integrity_check_and_vacuum_into(db_url: str, tmp_path: Path) -> None:
    upgrade_head(db_url)
    engine = create_app_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO app_meta (key, value) VALUES ('a', '1')"))
    assert integrity_check(engine) is True

    target = tmp_path / "snap" / "backup.db"
    vacuum_into(engine, target)
    assert target.exists()
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM app_meta WHERE key='a'") \
                   .fetchone()[0] == "1"
