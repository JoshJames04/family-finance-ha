"""An additive upgrade and rollback must retain existing duplicate-payroll guards."""

import sqlite3

from alembic import command
from alembic.script import ScriptDirectory

from app.persistence.migrate import alembic_config


def test_payroll_migration_roundtrip_preserves_prior_guards(tmp_path):
    path = tmp_path / "migration.db"
    config = alembic_config(f"sqlite:///{path}")
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1
    command.upgrade(config, "head")
    command.downgrade(config, "-1")
    with sqlite3.connect(path) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {"prevent_duplicate_payroll_insert", "prevent_duplicate_payroll_update"} <= names
    command.upgrade(config, "head")
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0073_payroll_verified_inputs"
        )


def test_per_test_session_never_writes_to_migrated_template(session, migrated_database):
    from app.persistence.models import Household

    session.add(Household(name="Synthetic isolation sentinel"))
    session.commit()
    with sqlite3.connect(migrated_database) as template:
        assert template.execute("SELECT count(*) FROM household").fetchone()[0] == 0
