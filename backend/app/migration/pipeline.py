"""Import pipeline: batch → stage → commit → rollback.

Idempotency: re-importing a file whose SHA-256 already has a committed
batch is a no-op returning the existing batch. Committed entities carry
``external_ref = imp:{batch}:{record}`` — re-commit cannot duplicate.

Extractors: a per-sheet protocol mapping ``import_record`` → domain
entity. Sheet extractors (#46–48) plug in here; a default no-op
extractor covers unmapped sheets.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session

from app.migration.parse import parse_workbook
from app.persistence.models import (
    ImportBatch,
    ImportRecord,
    ImportStatus,
    RowClass,
    Transaction,
    TransactionStatus,
)


def file_fingerprint(path: Path) -> tuple[str, str]:
    """(sha256, mtime-iso) — recorded per batch, never opens for write."""
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    return h, mtime


def existing_batch(
    session: Session, household_id: int, sha256: str
) -> ImportBatch | None:
    return session.execute(
        select(ImportBatch).where(
            ImportBatch.household_id == household_id,
            ImportBatch.sha256 == sha256,
            ImportBatch.status == ImportStatus.committed,
        )
    ).scalar_one_or_none()


def create_batch(
    session: Session,
    household_id: int,
    path: Path,
    actor: str,
) -> tuple[ImportBatch, int]:
    """Parse + stage records. Returns (batch, rows_read). Never writes
    domain entities — staging only."""
    sha, mtime = file_fingerprint(path)
    records = parse_workbook(path)
    batch = ImportBatch(
        household_id=household_id, filename=path.name, sha256=sha,
        file_mtime=mtime, actor=actor, status=ImportStatus.dry_run,
        rows_read=len(records),
        rows_flagged=sum(1 for r in records if r.review_flag),
    )
    session.add(batch)
    session.flush()
    session.add_all([
        ImportRecord(
            batch_id=batch.id, sheet=r.sheet, cell_range=r.cell_range,
            raw_value=r.raw,
            interpreted_value=json.dumps(
                [[c.col, c.raw] for c in r.values]),
            classification=r.classification, review_flag=r.review_flag,
        )
        for r in records
    ])
    session.flush()
    return batch, len(records)


class Extractor(Protocol):
    """Maps staged records of one sheet to domain entities."""

    sheet: str

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        """Return (entity_type, entity_id) or None to skip."""
        ...


def commit_batch(
    session: Session,
    batch: ImportBatch,
    extractors: list[Extractor] | None = None,
) -> int:
    """Commit staged records via extractors. Idempotent per record:
    a record with a target already set is skipped."""
    if batch.status == ImportStatus.committed:
        return 0
    by_sheet = {e.sheet: e for e in (extractors or [])}
    imported = 0
    for rec in batch.records:
        if rec.target_entity is not None:
            continue  # already mapped — idempotent re-commit
        if rec.classification != RowClass.actual:
            continue  # aggregates/unknown/flagged never synthesised
        ex = by_sheet.get(rec.sheet)
        if ex is None:
            continue  # unmapped sheet — stays staged for its extractor
        target = ex.map_record(session, batch, rec)
        if target is not None:
            rec.target_entity, rec.target_id = target
            imported += 1
    batch.status = ImportStatus.committed
    batch.committed_at = datetime.now()
    batch.rows_imported = imported
    session.flush()
    return imported


def rollback_batch(session: Session, batch: ImportBatch) -> int:
    """Void every entity produced by this batch (history preserved via
    import_record + void status — nothing is fabricated or destroyed)."""
    if batch.status != ImportStatus.committed:
        raise ValueError(f"cannot roll back batch in {batch.status}")
    txn_ids = [
        r.target_id for r in batch.records
        if r.target_entity == "transaction" and r.target_id is not None
    ]
    n = 0
    if txn_ids:
        n = int(cast(CursorResult[int], session.execute(
            update(Transaction)
            .where(Transaction.id.in_(txn_ids),
                   Transaction.status != TransactionStatus.reconciled)
            .values(status=TransactionStatus.void)
        )).rowcount or 0)
    for r in batch.records:
        r.target_entity = None
        r.target_id = None
    batch.status = ImportStatus.rolled_back
    batch.rolled_back_at = datetime.now()
    batch.rows_imported = 0
    session.flush()
    return n


def count_import_records(session: Session, batch_id: int) -> int:
    return int(session.execute(
        select(func.count(ImportRecord.id)).where(
            ImportRecord.batch_id == batch_id)
    ).scalar_one())
