"""Import pipeline: stage/commit/rollback/idempotency on the synthetic
fixture (issue #45)."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.migration.extractors import IncomeExtractor
from app.migration.parse import parse_workbook
from app.migration.pipeline import (
    commit_batch,
    count_import_records,
    create_batch,
    existing_batch,
    file_fingerprint,
    rollback_batch,
)
from app.persistence.models import (
    Account,
    AccountType,
    Household,
    ImportStatus,
    RowClass,
    Transaction,
    TransactionSource,
    TransactionStatus,
)
from tests.fixtures.synthetic_workbook import build_workbook


@pytest.fixture()
def wb_path(tmp_path: Path) -> Path:
    p = tmp_path / "synthetic.xlsx"
    build_workbook().save(p)
    return p


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Import",
                type=AccountType.personal, currency="GBP",
                opening_balance_pence=0, opening_date=date(2026, 1, 1))
    session.add(a)
    session.flush()
    return a


class TestParse:
    def test_records_have_provenance(self, wb_path: Path) -> None:
        recs = parse_workbook(wb_path)
        assert len(recs) > 100
        assert all(r.cell_range and r.sheet for r in recs)

    def test_classifications_cover_defects(
        self, wb_path: Path
    ) -> None:
        recs = parse_workbook(wb_path)
        by_class = {}
        for r in recs:
            by_class.setdefault(r.classification, []).append(r)
        # defect A/C — aggregates present
        assert by_class[RowClass.aggregate]
        # defect E — overwritten forecasts flagged
        assert any(r.sheet == "Budget-2027" and r.review_flag
                   for r in by_class.get(
                       RowClass.forecast_overwritten, []))
        # defect B — #REF! rows unknown+flagged
        assert any("#REF!" in r.raw
                   for r in by_class.get(RowClass.unknown, []))

    def test_header_rows_skipped(self, wb_path: Path) -> None:
        recs = parse_workbook(wb_path)
        income = [r for r in recs if r.sheet == "Income"]
        assert len(income) == 12  # header row skipped
        assert all(r.row_number >= 2 for r in income)


class TestPipeline:
    def test_dry_run_writes_nothing_but_batch(
        self, session: Session, household: Household, wb_path: Path
    ) -> None:
        batch, n = create_batch(session, household.id, wb_path, "tester")
        session.commit()
        assert batch.status == ImportStatus.dry_run
        assert batch.rows_read == n > 0
        # no domain entities created
        assert session.execute(
            select(func.count(Transaction.id))).scalar_one() == 0
        # staging rows exist with provenance
        assert count_import_records(session, batch.id) == n

    def test_commit_extracts_and_provenances(
        self, session: Session, household: Household, wb_path: Path,
        account: Account,
    ) -> None:
        batch, _ = create_batch(session, household.id, wb_path, "tester")
        n = commit_batch(session, batch,
                         [IncomeExtractor(account.id, 2026)])
        session.commit()
        assert n == 12
        txns = list(session.execute(
            select(Transaction).where(
                Transaction.source == TransactionSource.excel_import)
        ).scalars().all())
        assert len(txns) == 12
        assert all(t.import_record_id is not None
                   and t.external_ref is not None
                   and t.external_ref.startswith("imp:")
                   for t in txns)

    def test_reimport_is_noop(
        self, session: Session, household: Household, wb_path: Path,
        account: Account,
    ) -> None:
        b1, _ = create_batch(session, household.id, wb_path, "tester")
        commit_batch(session, b1, [IncomeExtractor(account.id, 2026)])
        session.commit()
        # fingerprint dedup: same file → existing committed batch
        sha, _ = file_fingerprint(wb_path)
        assert existing_batch(session, household.id, sha) == b1
        # and re-commit of a committed batch is a no-op
        assert commit_batch(session, b1,
                            [IncomeExtractor(account.id, 2026)]) == 0
        assert session.execute(
            select(func.count(Transaction.id))).scalar_one() == 12

    def test_rollback_voids_entities(
        self, session: Session, household: Household, wb_path: Path,
        account: Account,
    ) -> None:
        batch, _ = create_batch(session, household.id, wb_path, "tester")
        commit_batch(session, batch, [IncomeExtractor(account.id, 2026)])
        session.commit()
        n = rollback_batch(session, batch)
        session.commit()
        assert n == 12
        assert batch.status == ImportStatus.rolled_back
        voided = session.execute(
            select(func.count(Transaction.id)).where(
                Transaction.status == TransactionStatus.void)
        ).scalar_one()
        assert voided == 12
        # provenance survives rollback
        assert count_import_records(session, batch.id) == batch.rows_read

    def test_source_file_unchanged(
        self, session: Session, household: Household, wb_path: Path,
        account: Account,
    ) -> None:
        sha_before, mtime_before = file_fingerprint(wb_path)
        batch, _ = create_batch(session, household.id, wb_path, "tester")
        commit_batch(session, batch, [IncomeExtractor(account.id, 2026)])
        session.commit()
        sha_after, mtime_after = file_fingerprint(wb_path)
        assert (sha_before, mtime_before) == (sha_after, mtime_after)
