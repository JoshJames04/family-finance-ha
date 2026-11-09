"""Defect A — annual totals include intermediate monthly sums (#50).

AC: importer recomputes totals from line records; flag reports the
sheet-vs-recomputed delta exactly; no imported aggregate relies on the
sheet's own total formula.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.migration.pipeline import create_batch
from app.persistence.models import MigrationFlag
from tests.fixtures.synthetic_workbook import build_workbook


@pytest.fixture()
def wb_path(tmp_path: Path) -> Path:
    p = tmp_path / "wb.xlsx"
    build_workbook().save(p)
    return p


# true monthly sum in Bills-2026: Σ(12000 + (i%3)*500) for 12 months
TRUE_TOTAL = 144000 + 500 * 12  # 150000
INFLATED_TOTAL = TRUE_TOTAL * 2


def test_defect_a_flagged_with_exact_delta(
        session: Session, household, wb_path: Path) -> None:
    batch, _ = create_batch(session, household.id, wb_path, "tester")
    flags = session.execute(
        select(MigrationFlag).where(
            MigrationFlag.batch_id == batch.id,
            MigrationFlag.defect_id == "A")
    ).scalars().all()
    assert len(flags) == 1
    detail = json.loads(flags[0].detail or "{}")
    assert detail["sheet"] == "Bills-2026"
    assert detail["formula"] == "=SUM(B3:B26)"
    assert detail["recomputed_total"] == TRUE_TOTAL
    assert detail["sheet_total"] == INFLATED_TOTAL
    assert detail["delta"] == INFLATED_TOTAL - TRUE_TOTAL


def test_clean_total_not_flagged(
        session: Session, household, wb_path: Path) -> None:
    """B29 TRUE ANNUAL sums only literals → no flag (negative case)."""
    batch, _ = create_batch(session, household.id, wb_path, "tester")
    flagged = [f.detail for f in session.execute(
        select(MigrationFlag).where(
            MigrationFlag.batch_id == batch.id)).scalars().all()]
    assert not any("B29" in (d or "") or "TRUE ANNUAL" in (d or "")
                   for d in flagged)


def test_aggregates_never_imported(
        session: Session, household, wb_path: Path) -> None:
    """No aggregate row can become a transaction — the importer only
    commits RowClass.actual records, so sheet totals can't inflate
    the ledger even indirectly."""
    from sqlalchemy import func

    from app.persistence.models import ImportRecord, RowClass
    batch, _ = create_batch(session, household.id, wb_path, "tester")
    agg_count = session.execute(
        select(func.count(ImportRecord.id)).where(
            ImportRecord.batch_id == batch.id,
            ImportRecord.classification == RowClass.aggregate)
    ).scalar_one()
    mapped = session.execute(
        select(func.count(ImportRecord.id)).where(
            ImportRecord.batch_id == batch.id,
            ImportRecord.classification == RowClass.aggregate,
            ImportRecord.target_entity.is_not(None))
    ).scalar_one()
    assert agg_count > 0 and mapped == 0
