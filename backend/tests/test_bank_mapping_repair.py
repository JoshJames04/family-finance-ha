"""Synthetic legacy drift repair must never choose or discard a mapping."""

import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.fixture()
def database(tmp_path, migrated_database):
    path = tmp_path / "synthetic.db"
    shutil.copyfile(migrated_database, path)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            INSERT INTO household (id, name) VALUES (1, 'Synthetic'), (2, 'Other');
            INSERT INTO account (id, household_id, name, type, currency,
                opening_balance_pence, opening_date, archived)
                VALUES (1, 1, 'Synthetic', 'joint', 'GBP', 0, '2026-01-01', 0),
                       (2, 2, 'Other', 'joint', 'GBP', 0, '2026-01-01', 0);
            INSERT INTO bank_connection (id, household_id, provider, vault_ref, status, scopes)
                VALUES (1, 1, 'starling', 'synthetic-ref', 'connected', '');
            INSERT INTO "transaction" (id, account_id, date, amount_pence, payee, status, source)
                VALUES (42, 1, '2026-01-01', -123, 'Synthetic', 'cleared', 'manual');
        """)
    return path


def legacy(path, *, nullable=True, default=None):
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn, Operations(MigrationContext.configure(conn)).batch_alter_table(
        "bank_account_mapping"
    ) as batch:
        batch.alter_column("external_category_uid", existing_type=sa.String(64),
                           nullable=nullable, server_default=default)
    engine.dispose()


def mapping(path, row_id=7, category=None, account=1, household=1):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""INSERT INTO bank_account_mapping
            (id, household_id, connection_id, external_uid, external_label,
             external_category_uid, account_id, status)
            VALUES (?, ?, 1, 'synthetic-uid', 'Synthetic', ?, ?, 'linked')""",
                   (row_id, household, category, account))


def dump(path):
    with closing(sqlite3.connect(path)) as db, db:
        return list(db.iterdump())


def test_fresh_database_needs_no_repair_or_backup(database, tmp_path):
    from app.persistence.bank_mapping_repair import diagnose, repair

    assert diagnose(database)["needs_repair"] is False
    before = dump(database)
    assert repair(database, tmp_path / "backups") is None
    assert dump(database) == before
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_legacy_repair_preserves_ids_associations_history_and_verified_backup(
    database, tmp_path, journal_mode
):
    from app.persistence.backup import restore_from
    from app.persistence.bank_mapping_repair import diagnose, repair

    legacy(database)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(f"PRAGMA journal_mode={journal_mode}")
    mapping(database)
    mapping(database, row_id=8, category="synthetic-category")
    with closing(sqlite3.connect(database)) as db, db:
        db.executescript("""
            CREATE TABLE synthetic_mapping_reference (
                id INTEGER PRIMARY KEY,
                mapping_id INTEGER NOT NULL REFERENCES bank_account_mapping(id)
            );
            INSERT INTO synthetic_mapping_reference VALUES (11, 7);
        """)
    before = dump(database)
    report = diagnose(database)
    assert report["needs_repair"] is True
    assert report["null_categories"] == 1
    snapshot = repair(database, tmp_path / "backups")
    assert snapshot is not None
    # Snapshot retains the exact pre-repair records/schema (plus backup manifest).
    with closing(sqlite3.connect(snapshot)) as db, db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("SELECT id, account_id, external_category_uid FROM bank_account_mapping"
                          " ORDER BY id").fetchall() == [(7, 1, None), (8, 1, "synthetic-category")]
    with closing(sqlite3.connect(database)) as db, db:
        assert db.execute("SELECT id, account_id, external_category_uid FROM bank_account_mapping"
                          " ORDER BY id").fetchall() == [(7, 1, ""), (8, 1, "synthetic-category")]
        assert db.execute('SELECT id, amount_pence FROM "transaction"').fetchall() == [(42, -123)]
        assert db.execute("SELECT * FROM synthetic_mapping_reference").fetchall() == [(11, 7)]
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE bank_account_mapping SET external_category_uid=NULL WHERE id=7")
        db.execute("INSERT INTO bank_account_mapping (id, household_id, connection_id,"
                   " external_uid, external_label, status)"
                   " VALUES (9, 1, 1, 'new', 'Synthetic', 'proposed')")
        assert db.execute("SELECT external_category_uid FROM bank_account_mapping"
                          " WHERE id=9").fetchone() == ("",)
    assert diagnose(database)["needs_repair"] is False
    # Verified restore is the rollback: retain original ledger and null values.
    restore_from(snapshot, database)
    restored = [line for line in dump(database) if "ff_backup_manifest" not in line]
    assert sorted(restored) == sorted(before)
    assert repair(database, tmp_path / "backups") is not None


@pytest.mark.parametrize("second_category, second_account, second_household", [
    (None, 1, 1), ("", 1, 1), (None, 2, 1), (None, 1, 2),
])
def test_collisions_fail_before_any_normalization(database, tmp_path, second_category,
                                                 second_account, second_household):
    from app.persistence.bank_mapping_repair import diagnose, repair

    legacy(database)
    mapping(database)
    mapping(database, row_id=8, category=second_category, account=second_account,
            household=second_household)
    before = dump(database)
    assert diagnose(database)["collision_groups"] == 1
    with pytest.raises(RuntimeError, match="collision"):
        repair(database, tmp_path / "backups")
    assert dump(database) == before


@pytest.mark.parametrize("account, household", [(2, 1), (1, 2), (999, 1)])
def test_invalid_ownership_or_destination_is_never_inferred(database, tmp_path, account, household):
    from app.persistence.bank_mapping_repair import diagnose, repair

    legacy(database)
    mapping(database, account=account, household=household)
    before = dump(database)
    assert diagnose(database)["association_conflicts"] == 1
    with pytest.raises(RuntimeError, match="association"):
        repair(database, tmp_path / "backups")
    assert dump(database) == before


def test_failure_after_rebuild_rolls_back_schema_and_data(database, tmp_path, monkeypatch):
    from app.persistence import bank_mapping_repair

    legacy(database)
    mapping(database)
    before = dump(database)
    original = Operations.batch_alter_table

    @contextmanager
    def interrupt(*args, **kwargs):
        with original(*args, **kwargs) as batch:
            yield batch
        raise RuntimeError("synthetic interruption after table rebuild")

    monkeypatch.setattr(Operations, "batch_alter_table", interrupt)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        bank_mapping_repair.repair(database, tmp_path / "backups")
    assert dump(database) == before
    monkeypatch.setattr(Operations, "batch_alter_table", original)
    assert bank_mapping_repair.repair(database, tmp_path / "backups") is not None


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_writer_cannot_change_data_between_diagnosis_backup_and_repair(
    database, tmp_path, monkeypatch, journal_mode
):
    from app.persistence import bank_mapping_repair

    legacy(database)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(f"PRAGMA journal_mode={journal_mode}")
    mapping(database)
    original = bank_mapping_repair.backup.create_snapshot

    def concurrent_write(engine, backup_dir):
        with (closing(sqlite3.connect(database, timeout=0)) as writer,
              pytest.raises(sqlite3.OperationalError, match="locked")):
            writer.execute("UPDATE bank_account_mapping SET account_id=2")
        return original(engine, backup_dir)

    monkeypatch.setattr(bank_mapping_repair.backup, "create_snapshot", concurrent_write)
    assert bank_mapping_repair.repair(database, tmp_path / "backups") is not None
    with closing(sqlite3.connect(database)) as db, db:
        assert db.execute("SELECT account_id FROM bank_account_mapping").fetchall() == [(1,)]


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_process_death_after_rebuild_recovers_original_schema_and_data(
    database, tmp_path, journal_mode
):
    from app.persistence.bank_mapping_repair import repair

    legacy(database)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(f"PRAGMA journal_mode={journal_mode}")
    mapping(database)
    before = dump(database)
    script = """
import os
import sys
from pathlib import Path
from contextlib import contextmanager
from alembic.operations import Operations
from app.persistence.bank_mapping_repair import repair
original = Operations.batch_alter_table
@contextmanager
def interrupt(*args, **kwargs):
    with original(*args, **kwargs) as batch:
        yield batch
    os._exit(73)
Operations.batch_alter_table = interrupt
repair(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    result = subprocess.run([sys.executable, "-c", script, str(database),
                             str(tmp_path / "backups")], timeout=30, check=False)
    assert result.returncode == 73
    assert dump(database) == before
    assert repair(database, tmp_path / "backups") is not None


def test_unverified_backup_blocks_all_changes(database, tmp_path, monkeypatch):
    from app.persistence import bank_mapping_repair

    legacy(database)
    mapping(database)
    before = dump(database)
    monkeypatch.setattr(bank_mapping_repair.backup, "verify_snapshot", lambda _: False)
    with pytest.raises(RuntimeError, match="backup"):
        bank_mapping_repair.repair(database, tmp_path / "backups")
    assert dump(database) == before


def test_default_only_drift_is_detected_and_repaired(database, tmp_path):
    from app.persistence.bank_mapping_repair import diagnose, repair

    legacy(database, nullable=False)
    mapping(database, category="")
    report = diagnose(database)
    assert report["nullable"] is False
    assert report["server_default"] is None
    assert report["needs_repair"] is True
    assert repair(database, tmp_path / "backups") is not None
    assert diagnose(database)["needs_repair"] is False


def test_diagnostic_cli_is_read_only_and_does_not_create_missing_database(database, tmp_path):
    legacy(database)
    mapping(database)
    before = dump(database)
    result = subprocess.run([sys.executable, "-m", "app.persistence.bank_mapping_repair",
                             str(database)], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert '"needs_repair": true' in result.stdout
    assert '"server_default": null' in result.stdout
    assert "synthetic-uid" not in result.stdout
    assert dump(database) == before
    missing = tmp_path / "missing.db"
    result = subprocess.run([sys.executable, "-m", "app.persistence.bank_mapping_repair",
                             str(missing)], capture_output=True, check=False)
    assert result.returncode == 1
    assert not missing.exists()


def test_rediscovery_after_repair_reuses_existing_confirmed_mapping(database, tmp_path):
    from sqlalchemy.orm import Session

    from app.domain import discovery
    from app.domain.discovery import DiscoveredAccount
    from app.persistence.bank_mapping_repair import repair
    from app.persistence.models import BankConnection

    class SyntheticClient:
        def accounts(self, token):
            return [DiscoveredAccount("synthetic-uid", "Synthetic")]

    legacy(database)
    mapping(database)
    repair(database, tmp_path / "backups")
    engine = sa.create_engine(f"sqlite:///{database}")
    with Session(engine) as session:
        connection = session.get(BankConnection, 1)
        for _ in range(2):
            rows = discovery.discover(session, connection, SyntheticClient(), "synthetic-token")
            assert [(m.id, m.account_id, m.external_category_uid) for m in rows] == [(7, 1, "")]
        session.commit()
    engine.dispose()


def test_unrelated_existing_foreign_key_violation_blocks_repair(database, tmp_path):
    from app.persistence.bank_mapping_repair import repair

    legacy(database)
    mapping(database)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('UPDATE "transaction" SET account_id=999 WHERE id=42')
    before = dump(database)
    with pytest.raises(RuntimeError, match="Existing foreign-key"):
        repair(database, tmp_path / "backups")
    assert dump(database) == before


def test_unexpected_mapping_trigger_is_preserved_by_refusing_rebuild(database, tmp_path):
    from app.persistence.bank_mapping_repair import repair

    legacy(database)
    mapping(database)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TRIGGER synthetic_mapping_guard BEFORE DELETE ON bank_account_mapping"
                   " BEGIN SELECT RAISE(ABORT, 'synthetic retained guard'); END")
    before = dump(database)
    with pytest.raises(RuntimeError, match="Unexpected mapping triggers"):
        repair(database, tmp_path / "backups")
    assert dump(database) == before


@pytest.mark.parametrize("unsupported_ddl", [
    "CREATE UNIQUE INDEX synthetic_expression_guard ON bank_account_mapping(lower(external_uid))",
    "CREATE UNIQUE INDEX synthetic_collation_guard "
    "ON bank_account_mapping(external_uid COLLATE NOCASE)",
    "CREATE INDEX synthetic_descending ON bank_account_mapping(external_uid DESC)",
    "ALTER TABLE bank_account_mapping ADD COLUMN synthetic_extra TEXT COLLATE NOCASE",
])
def test_unsupported_index_or_collation_is_preserved_by_refusing_rebuild(
    database, tmp_path, unsupported_ddl
):
    from app.persistence.bank_mapping_repair import repair

    legacy(database)
    mapping(database)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(unsupported_ddl)
    before = dump(database)
    with pytest.raises(RuntimeError, match="index|collation"):
        repair(database, tmp_path / "backups")
    assert dump(database) == before
    assert not (tmp_path / "backups").exists()


def test_autoincrement_high_water_mark_is_preserved_by_refusing_rebuild(database, tmp_path):
    from app.persistence.bank_mapping_repair import repair

    legacy(database)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as conn, Operations(MigrationContext.configure(conn)).batch_alter_table(
        "bank_account_mapping", recreate="always", table_kwargs={"sqlite_autoincrement": True}
    ):
        pass
    engine.dispose()
    mapping(database)
    mapping(database, row_id=99, category="synthetic-deleted")
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("DELETE FROM bank_account_mapping WHERE id=99")
        assert db.execute("SELECT seq FROM sqlite_sequence WHERE name='bank_account_mapping'"
                          ).fetchone() == (99,)
    before = dump(database)
    with pytest.raises(RuntimeError, match="AUTOINCREMENT"):
        repair(database, tmp_path / "backups")
    assert dump(database) == before
    assert not (tmp_path / "backups").exists()
    with closing(sqlite3.connect(database)) as db, db:
        assert "AUTOINCREMENT" in db.execute(
            "SELECT sql FROM sqlite_master WHERE name='bank_account_mapping'"
        ).fetchone()[0]
        db.execute("INSERT INTO bank_account_mapping (household_id, connection_id,"
                   " external_uid, external_label, status)"
                   " VALUES (1, 1, 'new', 'Synthetic', 'proposed')")
        assert db.execute("SELECT id FROM bank_account_mapping WHERE external_uid='new'"
                          ).fetchone() == (100,)
