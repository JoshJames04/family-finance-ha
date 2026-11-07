"""Defects B–F — detection and register flags (#51–#55).

Each check produces migration_flag rows at staging time. Assertions use
the known synthetic-fixture values so deltas/residuals are exact.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.migration.pipeline import create_batch
from app.persistence.models import Household, MigrationFlag
from tests.fixtures.synthetic_workbook import build_workbook


@pytest.fixture()
def wb_path(tmp_path: Path) -> Path:
    p = tmp_path / "wb.xlsx"
    build_workbook().save(p)
    return p


def _flags(session: Session, batch_id: int,
           defect: str) -> list[dict]:
    rows = session.execute(
        select(MigrationFlag).where(
            MigrationFlag.batch_id == batch_id,
            MigrationFlag.defect_id == defect)
    ).scalars().all()
    return [json.loads(r.detail or "{}") for r in rows]


def _batch(session: Session, household: Household, path: Path):
    batch, _ = create_batch(session, household.id, path, "tester")
    return batch


# ---------- B: broken #REF! remaining balance -------------------------
def test_defect_b_reconstruction(session: Session, household, wb_path):
    batch = _batch(session, household, wb_path)
    flags = _flags(session, batch.id, "B")
    assert len(flags) == 1
    f = flags[0]
    assert f["sheet"] == "Family-Loan"
    # literals 2450000..2250000 step −50000; first #REF! at row 7
    assert f["last_good_value"] == 2250000
    assert f["reconstructed_value"] == 2200000
    assert f["residual"] == -50000
    assert f["period_delta"] == -50000


# ---------- C: stale annual totals ------------------------------------
def test_defect_c_delta_reported(session: Session, household, wb_path):
    batch = _batch(session, household, wb_path)
    flags = _flags(session, batch.id, "C")
    budget = [f for f in flags if f["sheet"] == "Budget-2024"]
    assert len(budget) == 1
    # monthly sum = Σ(180000 + i*1000) = 2226000; sheet literal 2400000
    assert budget[0]["monthly_sum"] == 2226000
    assert budget[0]["sheet_total"] == 2400000
    assert budget[0]["delta"] == 174000


def test_defect_c_no_false_positives_on_clean_sheets(
        session: Session, household, wb_path):
    """Christmas/Savings month runs have no stale literal below them."""
    batch = _batch(session, household, wb_path)
    sheets = {f["sheet"] for f in _flags(session, batch.id, "C")}
    assert "Christmas" not in sheets and "Savings" not in sheets


# ---------- D: hardcoded statutory rates ------------------------------
def test_defect_d_flags_every_hardcoded_rate(
        session: Session, household, wb_path):
    batch = _batch(session, household, wb_path)
    flags = _flags(session, batch.id, "D")
    # Payroll: PAYE 0.2 + NI 0.08 × 12 rows; Income: 0.76 × 12 rows
    payroll = [f for f in flags if f["sheet"] == "Payroll"]
    income = [f for f in flags if f["sheet"] == "Income"]
    assert len(payroll) == 24 and len(income) == 12
    # payroll rates exist in Tax-Tables → not divergent
    assert all(f["divergent"] is False for f in payroll)
    assert {f["hardcoded_rate"] for f in payroll} == {0.2, 0.08}
    # income's 0.76 is not a table rate → divergent
    assert all(f["divergent"] is True and f["hardcoded_rate"] == 0.76
               for f in income)


# ---------- E: overwritten forecasts -----------------------------------
def test_defect_e_flags_overwritten_rows(
        session: Session, household, wb_path):
    batch = _batch(session, household, wb_path)
    flags = _flags(session, batch.id, "E")
    assert len(flags) == 3  # Apr–Jun literals in Budget-2027
    assert all(f["sheet"] == "Budget-2027" for f in flags)
    assert {f["row"] for f in flags} == {5, 6, 7}
    assert all(f["overwritten_values"] for f in flags)


# ---------- F: out-of-range period references --------------------------
def test_defect_f_flags_out_of_range_refs(
        session: Session, household, wb_path):
    batch = _batch(session, household, wb_path)
    flags = _flags(session, batch.id, "F")
    assert len(flags) == 2
    assert all(f["sheet"] == "Payroll" for f in flags)
    refs = {f["ref"] for f in flags}
    assert refs == {"'Payroll-Old'!C14", "'Payroll-Old'!B14"}
    assert all(f["ref_row"] == 14 and
               f["last_valid_period_row"] == 13 for f in flags)


# ---------- register shape ----------------------------------------------
def test_all_six_defects_represented(
        session: Session, household, wb_path):
    batch = _batch(session, household, wb_path)
    ids = {r.defect_id for r in session.execute(
        select(MigrationFlag).where(
            MigrationFlag.batch_id == batch.id)).scalars().all()}
    assert ids == {"A", "B", "C", "D", "E", "F"}
    for r in session.execute(select(MigrationFlag)).scalars().all():
        assert r.status == "detected"
        assert json.loads(r.detail or "{}")
