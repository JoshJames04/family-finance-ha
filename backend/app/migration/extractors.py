"""Sheet extractors — map staged import_records to domain entities.

The framework ships with the Income extractor as the end-to-end proof;
per-sheet extractors for budgets/payroll/loans are issues #46–48
(blocked on the real workbook's confirmed structure).

Every entity created carries source=excel_import, the record's id in
``import_record_id``, and dedup ref ``imp:{batch}:{record}``.
"""

from __future__ import annotations

import json
import re
from calendar import monthrange
from datetime import date
from typing import Any

from openpyxl.utils import range_boundaries
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    Category,
    ImportBatch,
    ImportRecord,
    RowClass,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def record_values(record: ImportRecord) -> dict[int, Any]:
    return {c: v for c, v in json.loads(record.interpreted_value or "[]")}


class IncomeExtractor:
    """Income sheet rows: (Month, Employer, Gross, Net-formula)."""

    sheet = "Income"

    def __init__(self, account_id: int, year: int) -> None:
        self.account_id = account_id
        self.year = year

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        vals = record_values(record)
        month = _MONTHS.get(str(vals.get(1, ""))[:3])
        gross = vals.get(3)
        if month is None or not isinstance(gross, (int, float)):
            return None
        ref = f"imp:{batch.id}:{record.id}"
        t = Transaction(
            account_id=self.account_id,
            date=date(self.year, month, 1),
            amount_pence=int(gross),
            payee=str(vals.get(2, "excel import")),
            status=TransactionStatus.cleared,
            source=TransactionSource.excel_import,
            external_ref=ref, import_record_id=record.id,
        )
        session.add(t)
        session.flush()
        return "transaction", t.id


def already_imported(session: Session, batch_id: int,
                     record_id: int) -> int | None:
    return session.execute(
        select(Transaction.id).where(
            Transaction.external_ref == f"imp:{batch_id}:{record_id}")
    ).scalar_one_or_none()


# --- issue #46: annual/member budget sheets -------------------------------

# Excel table-name prefixes → section semantics. Prefix match only: the
# year suffix is unreliable (the 2027 sheet's tables carry '2026*' names
# — a workbook copy artifact), so the year is taken from the sheet's own
# parameter, never from table names.
_ROW_RE = re.compile(r"![A-Z]+(\d+)")
_SKIP_TABLES = ("tbltotals",)
_INCOME_TABLES = ("tblincome",)
_OWNER_TABLES = ("tblpersonal", "tbldaily")
_SHARED_TABLES = ("tblhouse", "tblshared", "tblother")


class BudgetYearExtractor:
    """One row of an annual budget sheet → a BudgetLine in each of up to
    12 monthly Budgets (the app's native period model — the workbook's
    JAN–DEC column grid mirrored faithfully rather than as one annual
    blob, so monthly variation survives migration).

    Sections are located by Excel-table membership, not row positions:
    ``tables`` maps table name → ref (e.g. ``{"tblShared2025":
    "B43:P48"}``) as captured in the structural inventory. Scope rules:
    income + personal/daily tables → owner-scoped lines; house/shared/
    other tables → shared household scope (owner_profile_id NULL);
    totals tables and unmapped rows are skipped (aggregates are never
    fabricated into lines — defect A/C).

    Set ``all_owner`` for a member-private sheet (e.g. the second
    member's budget) where every section belongs to that owner.

    Amounts are stored in the workbook as GBP decimals and converted to
    pence here. Each produced line's record keeps its provenance via
    ``ImportRecord.target_*`` and its original row classification.
    """

    # Forecast-overwritten rows are real budget data wearing a defect-E
    # flag: import them and let the record keep its classification for
    # the review queue. Aggregates/unknowns stay unsynthesised.
    accept_classes = {RowClass.actual, RowClass.forecast_overwritten}

    def __init__(
        self,
        sheet: str,
        *,
        household_id: int,
        owner_profile_id: int | None,
        year: int,
        name: str,
        tables: dict[str, str],
        all_owner: bool = False,
        label_col: int = 2,
        first_month_col: int = 3,
    ) -> None:
        self.sheet = sheet
        self.household_id = household_id
        self.owner_profile_id = owner_profile_id
        self.year = year
        self.name = name
        self.all_owner = all_owner
        self.label_col = label_col
        self.first_month_col = first_month_col
        self._row_tables: list[tuple[int, int, str]] = []
        for tname, ref in tables.items():
            _, r0, _, r1 = range_boundaries(ref)
            if r0 is None or r1 is None:
                continue  # unbounded whole-column refs carry no rows
            self._row_tables.append((r0, r1, tname.lower()))
        self._budgets: dict[tuple[int, int | None], Budget] = {}
        self._categories: dict[str, Category] = {}

    def _section(self, row: int) -> tuple[int | None, BudgetLineSide] | None:
        tname = next(
            (t for r0, r1, t in self._row_tables if r0 <= row <= r1),
            None)
        if tname is None:
            return None
        if tname.startswith(_SKIP_TABLES):
            return None
        if tname.startswith(_INCOME_TABLES):
            return self.owner_profile_id, BudgetLineSide.income
        if self.all_owner or tname.startswith(_OWNER_TABLES):
            return self.owner_profile_id, BudgetLineSide.expense
        if tname.startswith(_SHARED_TABLES):
            return None, BudgetLineSide.expense
        return None

    def _category(self, session: Session, label: str) -> Category:
        cat = self._categories.get(label)
        if cat is None:
            cat = session.execute(
                select(Category).where(
                    Category.household_id == self.household_id,
                    Category.parent_id.is_(None),
                    Category.name == label)
            ).scalar_one_or_none()
            if cat is None:
                cat = Category(household_id=self.household_id,
                               parent_id=None, name=label)
                session.add(cat)
                session.flush()
            self._categories[label] = cat
        return cat

    def _budget(self, session: Session, month: int,
                owner_id: int | None) -> Budget:
        key = (month, owner_id)
        budget = self._budgets.get(key)
        if budget is not None:
            return budget
        start = date(self.year, month, 1)
        end = date(self.year, month, monthrange(self.year, month)[1])
        scope = "shared" if owner_id is None else "personal"
        name = f"{self.name} {self.year}-{month:02d} ({scope})"
        budget = session.execute(
            select(Budget).where(
                Budget.household_id == self.household_id,
                Budget.name == name,
                Budget.start_date == start)
        ).scalar_one_or_none()
        if budget is None:
            budget = Budget(
                household_id=self.household_id,
                owner_profile_id=owner_id,
                name=name,
                period_type=BudgetPeriodType.monthly,
                start_date=start, end_date=end,
                status=BudgetStatus.approved)
            session.add(budget)
            session.flush()
        self._budgets[key] = budget
        return budget

    def _line(self, session: Session, budget: Budget, category: Category,
              side: BudgetLineSide, pence: int) -> BudgetLine:
        line = session.execute(
            select(BudgetLine).where(
                BudgetLine.budget_id == budget.id,
                BudgetLine.category_id == category.id)
        ).scalar_one_or_none()
        if line is None:
            line = BudgetLine(budget_id=budget.id,
                              category_id=category.id, side=side,
                              original_pence=pence)
            session.add(line)
            session.flush()
        else:  # idempotent re-map of an existing budget
            line.side = side
            line.original_pence = pence
        return line

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        vals = record_values(record)
        label = vals.get(self.label_col)
        months = {
            c - self.first_month_col + 1: float(v)
            for c, v in vals.items()
            if self.first_month_col <= c < self.first_month_col + 12
            and isinstance(v, (int, float))
        }
        if not isinstance(label, str) or not months:
            return None
        m = _ROW_RE.search(record.cell_range)
        if m is None:
            return None
        section = self._section(int(m.group(1)))
        if section is None:
            return None
        owner_id, side = section
        cat = self._category(session, label.strip())
        first: BudgetLine | None = None
        for month, amount in sorted(months.items()):
            line = self._line(
                session, self._budget(session, month, owner_id),
                cat, side, round(amount * 100))
            if first is None:
                first = line
        return ("budget_line", first.id) if first is not None else None
