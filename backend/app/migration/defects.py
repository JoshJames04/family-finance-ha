"""Defect detection — register checks run at staging time (issue #50).

Detection never trusts the sheet's own totals: every candidate total is
recomputed from literal line records and the delta is reported into
``migration_flag``. Flags are metadata, not financial rows — safe to
write during dry-run staging.

Implemented checks:
  A — a ``SUM`` range that swallows formula (subtotal) cells inflates
      the annual figure. Sheet total vs literal-leaf recompute → delta.
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


@dataclass(frozen=True)
class FlagDraft:
    defect_id: str
    detail: str  # JSON payload


def _eval_ref(v: Any, cells: dict[tuple[int, int], Any]) -> int | None:
    """Evaluate a single-cell reference formula (=B7) to its target's
    literal value, or None if not resolvable numerically."""
    if not isinstance(v, str):
        return None
    m = _REF.match(v)
    if not m:
        return None
    target = cells.get((int(m.group(2)),
                        column_index_from_string(m.group(1))))
    return int(target) if isinstance(target, int | float) else None


def detect_annual_total_defects(
        records: list[RowRecord]) -> list[FlagDraft]:
    """Defect A — annual totals including intermediate monthly sums."""
    cells: dict[tuple[str, int, int], Any] = {}
    for r in records:
        for c in r.values:
            cells[(r.sheet, r.row_number, c.col)] = c.raw
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
                    ref_val = _eval_ref(v, {
                        (rw, cc): val
                        for (sh, rw, cc), val in cells.items()
                        if sh == r.sheet})
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
