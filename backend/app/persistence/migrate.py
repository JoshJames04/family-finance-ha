"""Programmatic Alembic runner + version checks for readiness."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# Source checkout: backend/ sits two levels up. Installed image: alembic.ini
# + migrations/ are copied beside WORKDIR and FF_BACKEND_DIR points there.
BACKEND_DIR = Path(os.environ.get(
    "FF_BACKEND_DIR",
    Path(__file__).resolve().parent.parent.parent))


def alembic_config(db_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return cfg


def upgrade_head(db_url: str) -> None:
    cfg = alembic_config(db_url)
    cfg.attributes["db_url"] = db_url
    command.upgrade(cfg, "head")


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()


def current_revisions(engine: Engine) -> tuple[str, ...]:
    """Return every applied branch head, including an unmerged rollback state."""
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_heads()


def downgrade_one(config: Config) -> None:
    """Remove one applied leaf, choosing its branch explicitly at mergepoints."""
    overrides = dict(value.split("=", 1) for value in
                     (getattr(config.cmd_opts, "x", None) or []) if "=" in value)
    db_url = (overrides.get("db_url") or os.environ.get("FF_DATABASE_URL")
              or config.get_main_option("sqlalchemy.url"))
    if not db_url:
        raise ValueError("A database URL is required for migration rollback")
    engine = create_engine(db_url)
    try:
        heads = current_revisions(engine)
    finally:
        engine.dispose()
    if not heads:
        return
    head = sorted(heads)[-1]
    revision = ScriptDirectory.from_config(config).get_revision(head)
    assert revision is not None
    parents = revision.down_revision
    parent = (parents[-1] if isinstance(parents, tuple) else parents) or "base"
    # The qualifier prevents another branch sharing this parent being removed.
    command.downgrade(config, f"{head}@{parent}")


def head_revision(db_url: str) -> str | None:
    script = ScriptDirectory.from_config(alembic_config(db_url))
    return script.get_current_head()


def migrations_current(engine: Engine, db_url: str) -> bool:
    head = head_revision(db_url)
    return current_revisions(engine) == ((head,) if head is not None else ())
