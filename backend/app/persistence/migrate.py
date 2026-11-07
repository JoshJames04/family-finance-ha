"""Programmatic Alembic runner + version checks for readiness."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


def alembic_config(db_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return cfg


def upgrade_head(db_url: str) -> None:
    command.upgrade(alembic_config(db_url), "head")


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()


def head_revision(db_url: str) -> str | None:
    script = ScriptDirectory.from_config(alembic_config(db_url))
    return script.get_current_head()


def migrations_current(engine: Engine, db_url: str) -> bool:
    return current_revision(engine) == head_revision(db_url)
