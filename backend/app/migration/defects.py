"""Defect detection — register checks run at staging time (#50–#55).

Detection never trusts the sheet's own totals: every candidate total is
recomputed from literal line records and the delta is reported into
``migration_flag``. Flags are metadata, not financial rows — safe to
write during dry-run staging.

Implemented checks:
  A — a ``SUM`` range that swallows formula (subtotal) cells inflates
      the annual figure. Sheet total vs literal-leaf recompute → delta.
  B — ``#REF!`` remaining-balance run: reconstruct expected balance
      from the observed delta of the last literal values; flag the
      sheet's last good figure vs the reconstruction.
  C — literal annual total that disagrees with the sum of the monthly
      run above it; flag sheet value vs monthly-sum value.
  D — statutory-rate formulas with hardcoded constants (=Bn*0.2) that
      bypass the tax-tables sheet; flag each occurrence.
  E — forecast cells overwritten by literal actuals (already classified
      ``forecast_overwritten`` by the parser); one flag per row keeps
      the lost-forecast provenance in the register.
  F — cross-sheet references to periods outside the referenced sheet's
      month run (e.g. a 13th/14th period); flag each out-of-range ref.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openpyxl.utils import column_index_from_string
from sqlalchemy.orm import Session

from app.migration.parse import RowRecord
from app.persistence.models import ImportBatch, MigrationFlag, RowClass

_SUM = re.compile(r"^=SUM\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)$")
_REF = re.compile(r"^=([A-Z]+)(\d+)$")
_XREF = re.compile(r"'([^']+)'!([A-Z]+)(\d+)")
_RATE = re.compile(r"^=[A-Z]+\d+\*(0?\.\d+)$")
_MONTHS = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct",
    "nov", "dec",
}


@dataclass(frozen=True)
class FlagDraft:
    defect_id: str
    detail: str  # JSON payload


def _cell_map(records: list[RowRecord]
              ) -> dict[tuple[str, int, int], Any]:
    return {(r.sheet, r.row_number, c.col): c.raw
            for r in records for c in r.values}


def _eval_ref(v: Any, cells: dict[tuple[str, int, int], Any],
              sheet: str) -> int | None:
    if not isinstance(v, str):
        return None
    m = _REF.match(v)
    if not m:
        return None
    target = cells.get((sheet, int(m.group(2)),
                        column_index_from_string(m.group(1))))
    return int(target) if isinstance(target, int | float) else None


def detect_annual_total_defects(
        records: list[RowRecord]) -> list[FlagDraft]:
    """Defect A — annual totals including intermediate monthly sums."""
    cells = _cell_map(records)
    drafts: list[FlagDraft] = []
    for r in records:
        if r.classification != RowClass.aggregate:
            continue
        for cv in r.values:
            if not isinstance(cv.raw, str):
                continue
            m = _SUM.match(cv.raw)
            if not m:
                continue
            c1, r1, c2, r2 = m.groups()
            if c1 != c2:
                continue  # multi-column range — out of scope
            col = column_index_from_string(c1)
            literals, inflated, has_formula = 0, 0, False
            for rr in range(int(r1), int(r2) + 1):
                v = cells.get((r.sheet, rr, col))
                if isinstance(v, int | float):
                    literals += int(v)
                    inflated += int(v)
                elif isinstance(v, str) and v.startswith("="):
                    has_formula = True
                    ref_val = _eval_ref(v, cells, r.sheet)
                    if ref_val is not None:
                        inflated += ref_val
            if has_formula and inflated != literals:
                drafts.append(FlagDraft("A", json.dumps({
                    "sheet": r.sheet, "cell": r.cell_range,
                    "formula": cv.raw,
                    "sheet_total": inflated,
                    "recomputed_total": literals,
                    "delta": inflated - literals,
                })))
    return drafts


def detect_ref_balance_defects(
        records: list[RowRecord]) -> list[FlagDraft]:
    """Defect B — #REF! balance runs reconstructed from literal deltas.

    For each column, a run of literals followed by #REF! formulas: the
    expected value at the first broken cell is last_good + mean delta of
    the literal run. Flag carries last_good vs reconstructed."""
    cells = _cell_map(records)
    drafts: list[FlagDraft] = []
    cols = {(sh, c) for sh, _, c in cells}
    for sheet, col in cols:
        rows = sorted(rr for (sh, rr, cc) in cells
                      if sh == sheet and cc == col)
        literals: list[tuple[int, int]] = []
        for rr in rows:
            v = cells[(sheet, rr, col)]
            if isinstance(v, int | float):
                literals.append((rr, int(v)))
            elif isinstance(v, str) and "#REF!" in v:
                if len(literals) >= 2:
                    deltas = [b - a for (_, a), (_, b)
                              in zip(literals, literals[1:],
                                     strict=False)]
                    step = deltas[-1] if deltas else 0
                    last_row, last_good = literals[-1]
                    reconstructed = last_good + (rr - last_row) * step
                    drafts.append(FlagDraft("B", json.dumps({
                        "sheet": sheet, "cell_row": rr, "col": col,
                        "last_good_row": last_row,
                        "last_good_value": last_good,
                        "reconstructed_value": reconstructed,
                        "residual": reconstructed - last_good,
                        "period_delta": step,
                    })))
                break  # only flag the first broken cell per column run
    return drafts


def _month_runs(records: list[RowRecord],
                ) -> dict[str, list[tuple[int, int]]]:
    """sheet → [(first_row, last_row)] of consecutive month-labelled
    runs (≥4 rows) in column A."""
    out: dict[str, list[tuple[int, int]]] = {}
    by_sheet: dict[str, list[RowRecord]] = {}
    for r in records:
        by_sheet.setdefault(r.sheet, []).append(r)
    for sheet, rs in by_sheet.items():
        month_rows = sorted(
            r.row_number for r in rs
            if r.label.strip().lower() in _MONTHS)
        run_start: int | None = None
        prev = -2
        for rr in month_rows:
            if rr != prev + 1:
                if run_start is not None and prev - run_start >= 3:
                    out.setdefault(sheet, []).append((run_start, prev))
                run_start = rr
            prev = rr
        if run_start is not None and prev - run_start >= 3:
            out.setdefault(sheet, []).append((run_start, prev))
    return out


def detect_stale_annual_totals(
        records: list[RowRecord]) -> list[FlagDraft]:
    """Defect C — literal annual figure below a month run that does not
    equal the monthly sum."""
    cells = _cell_map(records)
    drafts: list[FlagDraft] = []
    for sheet, runs in _month_runs(records).items():
        for first, last in runs:
            below = last + 1
            cols = {c for (sh, rr, c) in cells
                    if sh == sheet and first <= rr <= last}
            for col in cols:
                if col == 1:
                    continue
                monthly = [cells.get((sheet, rr, col))
                           for rr in range(first, last + 1)]
                if not all(isinstance(v, int | float)
                           for v in monthly):
                    continue
                below_val = cells.get((sheet, below, col))
                if isinstance(below_val, int | float):
                    total = sum(int(v) for v in monthly
                                if isinstance(v, int | float))
                    if int(below_val) != total:
                        drafts.append(FlagDraft("C", json.dumps({
                            "sheet": sheet,
                            "cell_row": below, "col": col,
                            "sheet_total": int(below_val),
                            "monthly_sum": total,
                            "delta": int(below_val) - total,
                            "month_rows": [first, last],
                        })))
    return drafts


def detect_hardcoded_rates(records: list[RowRecord]) -> list[FlagDraft]:
    """Defect D — statutory-looking hardcoded rate formulas (=Bn*0.2).

    Rows where the hardcoded rate diverges from every rate published in
    a Tax-Tables-style sheet are marked ``divergent: true``."""
    cells = _cell_map(records)
    table_rates = sorted({
        v for (sh, _, _), v in cells.items()
        if "tax" in sh.lower() and isinstance(v, int | float)})
    seen: set[tuple[str, int, int]] = set()
    drafts: list[FlagDraft] = []
    for r in records:
        for cv in r.values:
            if not isinstance(cv.raw, str):
                continue
            m = _RATE.match(cv.raw)
            if not m:
                continue
            rate = float(m.group(1))
            key = (r.sheet, r.row_number, cv.col)
            if key in seen:
                continue
            seen.add(key)
            drafts.append(FlagDraft("D", json.dumps({
                "sheet": r.sheet, "cell_row": r.row_number,
                "col": cv.col, "formula": cv.raw,
                "hardcoded_rate": rate,
                "table_rates": table_rates,
                "divergent": (not table_rates or rate not in
                              table_rates),
            })))
    return drafts


def detect_overwritten_forecasts(
        records: list[RowRecord]) -> list[FlagDraft]:
    """Defect E — forecast cells overwritten by literal actuals."""
    return [
        FlagDraft("E", json.dumps({
            "sheet": r.sheet, "cell_range": r.cell_range,
            "row": r.row_number, "label": r.label,
            "overwritten_values": [
                [c.col, c.raw] for c in r.values
                if isinstance(c.raw, int | float)],
        }))
        for r in records
        if r.classification == RowClass.forecast_overwritten
    ]


def detect_out_of_range_periods(
        records: list[RowRecord]) -> list[FlagDraft]:
    """Defect F — cross-sheet references past the referenced sheet's
    month run (e.g. period 14 in a 12-period layout)."""
    runs = _month_runs(records)
    drafts: list[FlagDraft] = []
    for r in records:
        for cv in r.values:
            if not isinstance(cv.raw, str):
                continue
            for sheet2, col_s, row_s in _XREF.findall(cv.raw):
                target_run = runs.get(sheet2)
                if not target_run:
                    continue
                last_valid = target_run[-1][1]
                row_n = int(row_s)
                if row_n > last_valid:
                    drafts.append(FlagDraft("F", json.dumps({
                        "sheet": r.sheet, "cell_row": r.row_number,
                        "formula": cv.raw,
                        "ref": f"'{sheet2}'!{col_s}{row_s}",
                        "ref_row": row_n,
                        "last_valid_period_row": last_valid,
                    })))
    return drafts


def detect_all(records: list[RowRecord]) -> list[FlagDraft]:
    """Every register check, one pass."""
    out: list[FlagDraft] = []
    out += detect_annual_total_defects(records)
    out += detect_ref_balance_defects(records)
    out += detect_stale_annual_totals(records)
    out += detect_hardcoded_rates(records)
    out += detect_overwritten_forecasts(records)
    out += detect_out_of_range_periods(records)
    return out


def record_flags(
    session: Session, batch: ImportBatch, drafts: list[FlagDraft]
) -> int:
    """Persist defect drafts as migration_flag rows for the batch."""
    session.add_all([
        MigrationFlag(batch_id=batch.id, defect_id=d.defect_id,
                      status="detected", detail=d.detail)
        for d in drafts
    ])
    session.flush()
    return len(drafts)
