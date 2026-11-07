"""Read-only workbook parse → classified per-row records.

Opens with ``read_only=True`` + ``data_only=False`` so formulas are
visible (data_only=True would hide them behind cached results). The
source file is never opened for writing.

One :class:`RowRecord` per data row per sheet (spec: ``import_record``
per row carrying sheet/cell-range provenance). Pure label/header rows
are structure, not data, and are skipped.

Row classification (docs/MIGRATION.md §3):
  aggregate             — totals/subtotals (SUM formulas, total labels)
  forecast_overwritten  — literal value sitting in a formula column
  unknown               — uninterpretable (errors etc.) → review queue
  actual                — ordinary literal/formula data
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.read_only import EmptyCell
from openpyxl.utils import get_column_letter

from app.persistence.models import RowClass

_AGG_LABEL = re.compile(r"total|subtotal|annual", re.IGNORECASE)


@dataclass(frozen=True)
class CellValue:
    col: int
    raw: Any


@dataclass(frozen=True)
class RowRecord:
    sheet: str
    cell_range: str          # e.g. "Income!A2:E2"
    row_number: int
    label: str               # first-column label for extractor context
    raw: str                 # repr of raw cell values
    interpreted: str         # pipe-joined stringified values
    classification: RowClass
    review_flag: bool = False
    values: tuple[CellValue, ...] = field(default=(), compare=False)


def _rows(ws) -> list[tuple[int, dict[int, Any]]]:
    out = []
    for r in ws.iter_rows():
        real = [c for c in r if not isinstance(c, EmptyCell)
                and c.value is not None]
        if real:
            out.append((real[0].row,
                        {c.column: c.value for c in real}))
    return out


def _classify_row(
    sheet_name: str,
    label: str,
    values: dict[int, Any],
    formula_cols: set[int],
) -> tuple[RowClass, bool]:
    if any("#REF!" in str(v) for v in values.values()):
        return RowClass.unknown, True
    if _AGG_LABEL.search(label) or any(
            isinstance(v, str) and v.startswith(("=SUM(", "=SUBTOTAL("))
            for v in values.values()):
        return RowClass.aggregate, False
    if len(values) > 1 and all(
            isinstance(v, str) and v.startswith("=")
            for v in values.values()):
        return RowClass.aggregate, False  # fully computed row
    if any(col in formula_cols and isinstance(v, (int, float))
           for col, v in values.items()):
        return RowClass.forecast_overwritten, True
    if any(isinstance(v, (int, float, str)) for v in values.values()):
        return RowClass.actual, False
    return RowClass.unknown, True


def parse_workbook(path: Path) -> list[RowRecord]:
    """Classify every data row in every sheet (hidden included)."""
    wb = load_workbook(path, read_only=True, data_only=False)
    records: list[RowRecord] = []
    try:
        for ws in wb.worksheets:
            rows = _rows(ws)
            if not rows:
                continue
            # formula-dominant columns → literal values there are
            # forecast_overwritten (defect E)
            counts: dict[int, list[bool]] = {}
            for _, vals in rows:
                for c, v in vals.items():
                    counts.setdefault(c, []).append(
                        isinstance(v, str) and v.startswith("="))
            formula_cols = {
                c for c, f in counts.items()
                if len(f) >= 4 and sum(f) / len(f) > 0.5
            }
            header_row = rows[0][0]  # conventional: first row = headers
            for rownum, vals in rows:
                if rownum == header_row and all(
                        isinstance(v, str) for v in vals.values()):
                    continue  # header row is structure, not data
                label = str(vals.get(1, ""))
                data = {c: v for c, v in vals.items()}
                cls, flag = _classify_row(
                    ws.title, label, data, formula_cols)
                cols = sorted(data)
                rng = (f"{ws.title}!{get_column_letter(cols[0])}{rownum}:"
                       f"{get_column_letter(cols[-1])}{rownum}")
                records.append(RowRecord(
                    sheet=ws.title, cell_range=rng, row_number=rownum,
                    label=label,
                    raw=repr([data[c] for c in cols]),
                    interpreted="|".join(str(data[c]) for c in cols),
                    classification=cls, review_flag=flag,
                    values=tuple(CellValue(c, data[c]) for c in cols),
                ))
    finally:
        wb.close()
    return records
