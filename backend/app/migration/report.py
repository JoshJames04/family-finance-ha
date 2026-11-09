"""Reconciliation report + workbook-retirement gate (issue #49).

Proves (or disproves) migration fidelity per docs/MIGRATION.md §6:
per-sheet staged/classified/imported counts, defect-flag rollups,
loan reconstruction residuals, and period coverage. Workbook
retirement is gated on zero unresolved flags — a flag is unresolved
while its status is 'detected' or 'acknowledged'.

Amounts in the report are STRUCTURAL counts and residuals only —
the payload is safe to export for issue reports.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    Budget,
    BudgetLine,
    Gift,
    ImportBatch,
    ImportRecord,
    Loan,
    MigrationFlag,
    OvertimeEntry,
    PayrollRun,
    Repayment,
    SalaryRevision,
    SeasonalEvent,
    Transaction,
)

UNRESOLVED = ("detected", "acknowledged")

_ENTITY_COUNTS = (
    ("budget_lines", BudgetLine), ("budgets", Budget),
    ("salary_revisions", SalaryRevision),
    ("overtime_entries", OvertimeEntry),
    ("payroll_runs", PayrollRun), ("loans", Loan),
    ("repayments", Repayment), ("seasonal_events", SeasonalEvent),
    ("gifts", Gift), ("transactions", Transaction))


def reconciliation_report(
        session: Session,
        batch: ImportBatch) -> dict[str, Any]:
    """Structural fidelity report for one import batch."""
    recs = list(session.execute(
        select(ImportRecord).where(ImportRecord.batch_id == batch.id)
    ).scalars())
    by_sheet: dict[str, dict[str, Any]] = {}
    for r in recs:
        s = by_sheet.setdefault(r.sheet, {
            "records": 0, "imported": 0, "flagged": 0,
            "by_class": Counter()})
        s["records"] += 1
        s["by_class"][r.classification] += 1
        if r.target_entity is not None:
            s["imported"] += 1
        if r.review_flag:
            s["flagged"] += 1
    for s in by_sheet.values():
        s["by_class"] = dict(s["by_class"])

    flags = list(session.execute(
        select(MigrationFlag).where(MigrationFlag.batch_id == batch.id)
    ).scalars())
    flag_rollup = dict(Counter(f.defect_id for f in flags))
    unresolved = sum(1 for f in flags if f.status in UNRESOLVED)

    entities = {
        name: int(session.execute(
            select(func.count(model.id))).scalar_one())
        for name, model in _ENTITY_COUNTS}

    loans = list(session.execute(select(Loan)).scalars())
    loan_summary = [{
        "loan_id": ln.id, "party": "redacted",
        "repayments": len(ln.repayments),
        "residual_pence": ln.principal_pence
        - sum(r.amount_pence for r in ln.repayments),
    } for ln in loans]

    budgets = list(session.execute(select(Budget)).scalars())
    runs = list(session.execute(select(PayrollRun)).scalars())
    coverage = {
        "budget_periods": sorted({
            b.start_date.strftime("%Y-%m") for b in budgets}),
        "payroll_periods": sorted({
            r.period_start.strftime("%Y-%m") for r in runs}),
    }

    return {
        "batch": {
            "id": batch.id, "filename": batch.filename,
            "sha256": batch.sha256, "status": batch.status,
            "rows_read": batch.rows_read,
            "rows_imported": batch.rows_imported,
        },
        "sheets": by_sheet,
        "entities_created": entities,
        "defect_flags": {
            "by_defect": flag_rollup, "total": len(flags),
            "unresolved": unresolved},
        "loan_reconstruction": loan_summary,
        "period_coverage": coverage,
        "retirement_ready": unresolved == 0
        and batch.status == "committed",
    }


def retirement_gate(
        session: Session,
        batch: ImportBatch) -> dict[str, Any]:
    """Workbook-retirement gate: every flag must be resolved and the
    batch committed. The gate is the single check that lets the
    spreadsheet be retired (#56 reads this)."""
    unresolved = int(session.execute(
        select(func.count(MigrationFlag.id)).where(
            MigrationFlag.batch_id == batch.id,
            MigrationFlag.status.in_(UNRESOLVED))).scalar_one())
    flagged_records = int(session.execute(
        select(func.count(ImportRecord.id)).where(
            ImportRecord.batch_id == batch.id,
            ImportRecord.review_flag.is_(True))).scalar_one())
    return {
        "batch_id": batch.id,
        "committed": batch.status == "committed",
        "unresolved_flags": unresolved,
        "flagged_records": flagged_records,
        "ready": batch.status == "committed" and unresolved == 0,
    }
