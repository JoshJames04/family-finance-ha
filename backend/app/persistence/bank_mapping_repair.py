"""Explicit, offline SQLite diagnostic/repair for legacy category-key drift (#550).

Run from backend: python -m app.persistence.bank_mapping_repair DATABASE
Add --repair --backup-dir DIRECTORY only after reviewing the diagnostic.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import TypedDict

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from app.persistence import backup


class Diagnosis(TypedDict):
    nullable: bool
    server_default: str | None
    needs_repair: bool
    null_categories: int
    collision_groups: int
    association_conflicts: int


def _engine(path: Path, mode: str) -> Engine:
    # Never create a missing DB or interpret arbitrary strings as database URLs.
    uri = path.resolve(strict=True).as_uri() + f"?mode={mode}"
    return sa.create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True),
                            poolclass=NullPool)


def _diagnose(conn: Connection) -> Diagnosis:
    inspector = sa.inspect(conn)
    column = next(c for c in inspector.get_columns("bank_account_mapping")
                  if c["name"] == "external_category_uid")
    keys = inspector.get_unique_constraints("bank_account_mapping")
    if (len(keys) != 1 or keys[0]["name"] != "uq_mapping_conn_ext" or
            keys[0]["column_names"] !=
            ["connection_id", "external_uid", "external_category_uid"]):
        raise RuntimeError("Unexpected mapping unique key; stop and investigate schema provenance")
    collisions = conn.execute(sa.text("""
        SELECT COUNT(*) FROM (
            SELECT 1 FROM bank_account_mapping
            GROUP BY connection_id, external_uid, COALESCE(external_category_uid, '')
            HAVING COUNT(*) > 1
        )
    """)).scalar_one()
    conflicts = conn.execute(sa.text("""
        SELECT COUNT(*) FROM bank_account_mapping m
        LEFT JOIN bank_connection c ON c.id=m.connection_id
        LEFT JOIN account a ON a.id=m.account_id
        WHERE c.id IS NULL OR c.household_id != m.household_id
           OR (m.account_id IS NOT NULL AND
               (a.id IS NULL OR a.household_id != m.household_id))
           OR (m.status='linked' AND m.account_id IS NULL)
    """)).scalar_one()
    default = column["default"]
    return {
        "nullable": column["nullable"],
        "server_default": default,
        "needs_repair": column["nullable"] or default not in ("''", '""', "('')", '("")'),
        "null_categories": conn.execute(sa.text(
            "SELECT COUNT(*) FROM bank_account_mapping WHERE external_category_uid IS NULL"
        )).scalar_one(),
        "collision_groups": collisions,
        "association_conflicts": conflicts,
    }


def diagnose(path: Path) -> Diagnosis:
    """Read-only schema/data assessment; outputs counts, never provider identifiers."""
    engine = _engine(path, "ro")
    try:
        with engine.connect() as conn:
            return _diagnose(conn)
    finally:
        engine.dispose()


def repair(path: Path, backup_dir: Path) -> Path | None:
    """Repair only unambiguous drift; return verified snapshot or None for no change.

    Stop the application first. BEGIN IMMEDIATE blocks other writers before both
    diagnosis and snapshot. Snapshot uses an independent read connection (backing
    up the write connection itself can hang). Explicit BEGIN also makes SQLite
    table recreation transactional, including process interruption recovery.
    """
    engine = _engine(path, "rw")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                report = _diagnose(conn)
                if report["collision_groups"] or report["association_conflicts"]:
                    raise RuntimeError(
                        f"Repair refused: {report['collision_groups']} collision groups; "
                        f"{report['association_conflicts']} association conflicts. "
                        "Review the local diagnostic queries in docs/MIGRATION.md; "
                        "resolve ownership/destinations explicitly before retrying. "
                        "No rows changed."
                    )
                if not report["needs_repair"]:
                    conn.rollback()
                    return None
                # Reflection drops expression indexes, ordering and collations.
                # Refuse unsupported DDL rather than silently weakening guards.
                table_sql = conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='table' "
                    "AND name='bank_account_mapping'"
                ).scalar_one()
                if re.search(r"\b(AUTOINCREMENT|STRICT)\b|\bWITHOUT\s+ROWID\b",
                             table_sql, re.IGNORECASE):
                    raise RuntimeError(
                        "Unsupported mapping table option "
                        "(AUTOINCREMENT, STRICT or WITHOUT ROWID); "
                        "investigate before rebuilding"
                    )
                if re.search(r"\bCOLLATE\b", table_sql, re.IGNORECASE):
                    raise RuntimeError(
                        "Custom mapping column collation; investigate before rebuilding"
                    )
                for index in conn.exec_driver_sql(
                    "SELECT name FROM pragma_index_list('bank_account_mapping')"
                ).scalars():
                    details = conn.execute(sa.text(
                        "SELECT cid, desc, coll FROM pragma_index_xinfo(:name) WHERE key=1"
                    ), {"name": index}).all()
                    if any(cid < 0 or descending or collation != "BINARY"
                           for cid, descending, collation in details):
                        raise RuntimeError(
                            "Unsupported mapping index expression, order or collation; "
                            "investigate before rebuilding"
                        )
                if conn.exec_driver_sql(
                    "SELECT 1 FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name='bank_account_mapping'"
                ).first():
                    raise RuntimeError("Unexpected mapping triggers; investigate before rebuilding")
                if any(check["name"] is None for check in
                       sa.inspect(conn).get_check_constraints("bank_account_mapping")):
                    raise RuntimeError("Unnamed mapping checks; investigate before rebuilding")
                if conn.exec_driver_sql("PRAGMA foreign_key_check").first():
                    raise RuntimeError("Existing foreign-key violations; investigate before repair")
                snapshot = backup.create_snapshot(engine, backup_dir)
                if not backup.verify_snapshot(snapshot):
                    raise RuntimeError("Pre-repair backup verification failed; no rows changed")
                mapping_count = conn.exec_driver_sql(
                    "SELECT COUNT(*) FROM bank_account_mapping"
                ).scalar_one()
                conn.exec_driver_sql(
                    "UPDATE bank_account_mapping SET external_category_uid='' "
                    "WHERE external_category_uid IS NULL"
                )
                with Operations(MigrationContext.configure(conn)).batch_alter_table(
                    "bank_account_mapping"
                ) as batch:
                    batch.alter_column("external_category_uid", existing_type=sa.String(64),
                                       nullable=False, server_default="")
                if conn.exec_driver_sql(
                    "SELECT COUNT(*) FROM bank_account_mapping"
                ).scalar_one() != mapping_count:
                    raise RuntimeError("Post-repair mapping row count changed; rolling back")
                if _diagnose(conn)["needs_repair"]:
                    raise RuntimeError("Post-repair constraint verification failed")
                if conn.exec_driver_sql("PRAGMA foreign_key_check").first():
                    raise RuntimeError("Post-repair foreign-key verification failed")
                if conn.exec_driver_sql("PRAGMA integrity_check").all() != [("ok",)]:
                    raise RuntimeError("Post-repair integrity verification failed")
                conn.commit()
                return snapshot
            except BaseException:
                conn.rollback()
                raise
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.repair and args.backup_dir is None:
        parser.error("--repair requires --backup-dir")
    try:
        print(json.dumps(diagnose(args.database), sort_keys=True))
        if args.repair:
            snapshot = repair(args.database, args.backup_dir)
            print(f"Verified pre-repair backup: {snapshot}" if snapshot else "No repair needed")
    except (OSError, RuntimeError, sa.exc.SQLAlchemyError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
