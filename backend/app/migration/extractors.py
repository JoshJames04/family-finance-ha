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
from datetime import date, timedelta
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
    DayType,
    Gift,
    ImportBatch,
    ImportRecord,
    Loan,
    LoanDirection,
    OvertimeEntry,
    OvertimeStatus,
    PayrollComponent,
    PayrollComponentKind,
    PayrollRun,
    PayrollRunStatus,
    Repayment,
    RowClass,
    SalaryRevision,
    SeasonalEvent,
    TaxRuleSet,
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


# --- issue #47: salary / overtime / payroll / tax tables ------------------

def _as_date(v: Any) -> date | None:
    """Staged cell → date. Datetimes serialise as 'YYYY-MM-DD …'."""
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _pay_date(work: date) -> date:
    """The workbook's payroll attribution rule (replicated, not read
    from its formula — defect F): worked before the 15th → paid on the
    28th of the same month, else the 28th of next month; a weekend 28th
    snaps back to Friday."""
    if work.day < 15:
        base = date(work.year, work.month, 28)
    elif work.month == 12:
        base = date(work.year + 1, 1, 28)
    else:
        base = date(work.year, work.month + 1, 28)
    return (base - timedelta(days=base.weekday() - 4)
            if base.weekday() > 4 else base)


def cached_values(path: str) -> dict[tuple[str, int, int], Any]:
    """Excel's last-calculated formula results, keyed
    (sheet, row, col). Staged records deliberately keep formulas
    visible (data_only=False); extractors resolve a formula cell's
    *value* through this map. The workbook is opened read-only here
    too — nothing is written back."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        return {(ws.title, c.row, c.column): c.value
                for ws in wb.worksheets
                for row in ws.iter_rows() for c in row
                if c.value is not None}
    finally:
        wb.close()


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _cached_num(cache: dict[tuple[str, int, int], Any],
                sheet: str, row: int,
                col: int) -> float | None:
    v = cache.get((sheet, row, col))
    return float(v) if isinstance(v, (int, float)) else None


class SalaryHistoryExtractor:
    """tblEmploymentHistory rows → effective-dated SalaryRevision.
    Surrounding payroll parameters (pension %/type, loan plans, NI
    category, weekly hours) ride along in ``notes`` as JSON — they are
    financially meaningful context the flat revision model lacks
    columns for."""

    sheet: str

    def __init__(self, sheet: str, employment_id: int) -> None:
        self.sheet = sheet
        self.employment_id = employment_id

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        vals = record_values(record)
        eff, salary = _as_date(vals.get(2)), _num(vals.get(3))
        if eff is None or salary is None:
            return None
        rev = session.execute(
            select(SalaryRevision).where(
                SalaryRevision.employment_id == self.employment_id,
                SalaryRevision.effective_date == eff)
        ).scalar_one_or_none()
        if rev is None:
            rev = SalaryRevision(employment_id=self.employment_id,
                                 effective_date=eff,
                                 annual_salary_pence=0)
            session.add(rev)
        rev.annual_salary_pence = round(salary * 100)
        rev.notes = json.dumps({
            "pension_pct": vals.get(4), "pension_type": vals.get(5),
            "student_loan_plan": vals.get(6),
            "postgrad_loan_plan": vals.get(7),
            "ni_category": vals.get(8), "weekly_hours": vals.get(9),
            "source": "excel_import"})
        session.flush()
        return "salary_revision", rev.id


class OvertimeExtractor:
    """tblOvertime rows → OvertimeEntry. Date/hours are literals;
    multiplier + hourly rate resolve through ``cache`` when the cell
    holds a formula. Day type derives from the worked date; the pay
    period is recomputed with the workbook's own 15th/28th rule
    (``_pay_date``) and recorded in notes — never the workbook's
    possibly-stale 'Payroll Date' formula result."""

    def __init__(self, sheet: str, employment_id: int,
                 cache: dict[tuple[str, int, int], Any],
                 today: date | None = None) -> None:
        self.sheet = sheet
        self.employment_id = employment_id
        self.cache = cache
        self.today = today or date.today()

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        vals = record_values(record)
        work, hours = _as_date(vals.get(2)), _num(vals.get(5))
        if work is None or hours is None or hours <= 0:
            return None
        m = _ROW_RE.search(record.cell_range)
        row = int(m.group(1)) if m else 0
        mult = _num(vals.get(6)) or _cached_num(
            self.cache, self.sheet, row, 6)
        rate = _num(vals.get(7)) or _cached_num(
            self.cache, self.sheet, row, 7)
        if mult is None or rate is None:
            return None
        day_type = (DayType.saturday if work.weekday() == 5 else
                    DayType.sunday if work.weekday() == 6 else
                    DayType.weekday)
        actual = _as_date(vals.get(4))
        entry = OvertimeEntry(
            employment_id=self.employment_id,
            work_date=work, hours=hours,
            multiplier_bps=round(mult * 10_000),
            day_type=day_type,
            status=(OvertimeStatus.approved if work <= self.today
                    else OvertimeStatus.draft),
            hourly_rate_pence=round(rate * 100),
            notes=json.dumps({
                "payroll_date": _pay_date(work).isoformat(),
                "actual_date": actual.isoformat() if actual else None,
                "source": "excel_import"}))
        session.add(entry)
        session.flush()
        return "overtime_entry", entry.id


class TaxTablesExtractor:
    """The three reference tables (tblPAYE_Bands / tblNI_Bands /
    tblStudentLoanPlans) → versioned TaxRuleSet.params JSON per tax
    year. Hard-coded formula constants are never imported as rules
    (defect D) — only literal band rows qualify. Each row upserts its
    year's set, so partial imports stay consistent."""

    def __init__(self, sheet: str, jurisdiction: str,
                 tables: dict[str, str]) -> None:
        self.sheet = sheet
        self.jurisdiction = jurisdiction
        # (row bounds, col bounds, name) — the sheet packs PAYE + NI
        # side-by-side so one staged row record spans two tables and
        # must be partitioned by column, not just row.
        self._tables: list[tuple[int, int, int, int, str]] = []
        for tname, ref in tables.items():
            c0, r0, c1, r1 = range_boundaries(ref)
            if r0 is None or r1 is None or c0 is None or c1 is None:
                continue
            self._tables.append((r0, r1, c0, c1, tname.lower()))
        self._bands: dict[str, dict[str, list[Any]]] = {}

    def _upsert(self, session: Session, year: str) -> TaxRuleSet:
        rs = session.execute(
            select(TaxRuleSet).where(
                TaxRuleSet.tax_year == year,
                TaxRuleSet.jurisdiction == self.jurisdiction,
                TaxRuleSet.version == 1)
        ).scalar_one_or_none()
        if rs is None:
            rs = TaxRuleSet(tax_year=year,
                            jurisdiction=self.jurisdiction,
                            version=1, params="{}")
            session.add(rs)
        rs.params = json.dumps(self._bands[year], sort_keys=True)
        session.flush()
        return rs

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        m = _ROW_RE.search(record.cell_range)
        row = int(m.group(1)) if m else 0
        vals = record_values(record)
        target: tuple[str, int] | None = None
        for r0, r1, c0, c1, tname in self._tables:
            if not r0 <= row <= r1:
                continue
            cells = {c: v for c, v in vals.items() if c0 <= c <= c1}
            if not cells or any(
                    isinstance(v, str) and v.startswith("=")
                    for v in cells.values()):
                continue  # defect D guard: formulas never become rules
            year = str(cells.get(2) or cells.get(8) or "")
            if not year or year.lower().startswith("tax"):
                continue
            acc = self._bands.setdefault(
                year, {"paye": [], "ni": [], "student_loan": []})
            if tname.startswith("tblpaye"):
                acc["paye"].append({"order": _num(cells.get(3)),
                                    "limits": [_num(cells.get(4)),
                                               _num(cells.get(5))],
                                    "rate": _num(cells.get(6))})
            elif tname.startswith("tblni"):
                acc["ni"].append({"category": cells.get(9),
                                  "order": _num(cells.get(10)),
                                  "limits": [_num(cells.get(11)),
                                             _num(cells.get(12))],
                                  "rate": _num(cells.get(13))})
            elif tname.startswith("tblstudent"):
                acc["student_loan"].append(
                    {"plan": cells.get(3),
                     "threshold": _num(cells.get(4)),
                     "rate": _num(cells.get(5))})
            else:
                continue
            rs = self._upsert(session, year)
            target = target or ("tax_rule_set", rs.id)
        return target


class PayrollExtractor:
    """tblMonthlyModel rows → PayrollRun + PayrollComponent lines.
    Inputs are literals; computed amounts resolve through ``cache``
    (Excel's saved results — the model's whole output is formulas).
    Status is recomputed by pay date (reconciled when paid, else
    forecast) — the workbook's own status cells are not trusted."""

    # Monthly Model rows are formula-dominant (computed payroll output)
    # so they stage as aggregate — they ARE the data here, resolved via
    # cache, not fabricated totals.
    accept_classes = {RowClass.actual, RowClass.forecast_overwritten,
                      RowClass.aggregate}

    def __init__(self, sheet: str, employment_id: int, jurisdiction: str,
                 cache: dict[tuple[str, int, int], Any],
                 today: date | None = None) -> None:
        self.sheet = sheet
        self.employment_id = employment_id
        self.jurisdiction = jurisdiction
        self.cache = cache
        self.today = today or date.today()

    def _component(self, run: PayrollRun, row: int, col: int,
                   kind: PayrollComponentKind, label: str,
                   reconciled: bool) -> None:
        v = _cached_num(self.cache, self.sheet, row, col)
        if v is None or v == 0:
            return
        pence = round(v * 100)
        run.components.append(PayrollComponent(
            kind=kind, label=label, forecast_pence=pence,
            actual_pence=pence if reconciled else None))

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        vals = record_values(record)
        month = _as_date(vals.get(2))
        if month is None:
            return None
        m = _ROW_RE.search(record.cell_range)
        row = int(m.group(1)) if m else 0
        gross = _cached_num(self.cache, self.sheet, row, 17)
        net = _cached_num(self.cache, self.sheet, row, 27)
        if gross is None or net is None:
            return None
        start = date(month.year, month.month, 1)
        end = date(month.year, month.month,
                   monthrange(month.year, month.month)[1])
        # payroll month M is paid on the (Friday-snapped) 28th of M
        pay = _pay_date(date(month.year, month.month, 1))
        ty = self.cache.get((self.sheet, row, 3)) or (
            f"{month.year}/{month.year + 1 - 2000}"
            if month.month >= 4 else
            f"{month.year - 1}/{month.year - 2000}")
        version = session.execute(
            select(TaxRuleSet.version).where(
                TaxRuleSet.tax_year == str(ty),
                TaxRuleSet.jurisdiction == self.jurisdiction)
            .order_by(TaxRuleSet.version.desc())
        ).scalars().first() or 1
        reconciled = pay <= self.today
        run = session.execute(
            select(PayrollRun).where(
                PayrollRun.employment_id == self.employment_id,
                PayrollRun.period_start == start,
                PayrollRun.period_end == end,
                PayrollRun.corrects_id.is_(None))
        ).scalar_one_or_none()
        if run is None:
            run = PayrollRun(
                employment_id=self.employment_id,
                period_start=start, period_end=end, pay_date=pay,
                status=(PayrollRunStatus.reconciled if reconciled
                        else PayrollRunStatus.forecast),
                tax_year=str(ty), rule_set_version=version,
                gross_pence=round(gross * 100),
                net_pence=round(net * 100))
            session.add(run)
            session.flush()
        K = PayrollComponentKind
        for col, kind, label in (
                (16, K.overtime, "Overtime"), (7, K.bonus, "Bonus"),
                (19, K.allowance, "Allowance"),
                (9, K.allowance, "Manual adjustment"),
                (8, K.unpaid_leave, "Unpaid leave"),
                (18, K.pension, "Pension"), (22, K.tax, "PAYE"),
                (24, K.ni, "NI"), (25, K.student_loan, "Student loan")):
            self._component(run, row, col, kind, label, reconciled)
        session.flush()
        return "payroll_run", run.id


# --- issue #48: loan ledger / christmas / slow boat ------------------------

class LoanLedgerExtractor:
    """Family loan sheet → Loan + Repayment rows. The workbook's
    'Remaining' formula column is *never read* (defect B — the stored
    balance is broken); the balance is reconstructed as principal −
    validated repayments and any residual is flagged in notes. The
    'Total Amount' summary row supplies the principal."""

    def __init__(self, sheet: str, household_id: int, party: str,
                 direction: LoanDirection = LoanDirection.we_owe,
                 principal_label: str = "total amount") -> None:
        self.sheet = sheet
        self.household_id = household_id
        self.party = party
        self.direction = direction
        self.principal_label = principal_label
        self._loan: Loan | None = None
        self._principal: int | None = None

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        vals = record_values(record)
        label = str(vals.get(2, "")).strip().lower()
        if label.startswith(self.principal_label):
            self._principal = round(
                float(next((v for c, v in vals.items()
                            if c > 2 and isinstance(v, (int, float))),
                           0)) * 100)
            return None
        year = vals.get(2)
        amount = _num(vals.get(3))
        if (not isinstance(year, (int, float)) or amount is None
                or amount <= 0):
            return None  # zero rows are skipped years, not repayments
        if self._principal is None:
            record.review_flag = True  # repayments before principal
            return None
        if self._loan is None:
            self._loan = Loan(
                household_id=self.household_id, party=self.party,
                direction=self.direction,
                principal_pence=self._principal,
                start_date=date(int(year), 1, 1),
                notes="balance reconstructed from validated repayments")
            session.add(self._loan)
            session.flush()
        rep = Repayment(loan_id=self._loan.id,
                        date=date(int(year), 12, 31),
                        amount_pence=round(amount * 100),
                        notes="excel import")
        session.add(rep)
        session.flush()
        paid = sum(r.amount_pence for r in self._loan.repayments)
        residual = self._loan.principal_pence - paid
        self._loan.notes = (
            "balance reconstructed: principal − repayments = "
            f"{residual}p residual" + (" (flagged)" if residual < 0
                                      else ""))
        return "repayment", rep.id


class XmasExtractor:
    """xmasYYYY tables → SeasonalEvent(year, christmas) + Gift rows.
    Each side-by-side table carries its year in the table name; event
    budget = sum of planned amounts (recomputed, not read)."""

    def __init__(self, sheet: str, household_id: int,
                 tables: dict[str, str]) -> None:
        self.sheet = sheet
        self.household_id = household_id
        self._tables: list[tuple[int, int, int, int, int]] = []
        for tname, ref in tables.items():
            c0, r0, c1, r1 = range_boundaries(ref)
            m = re.search(r"(\d{4})", tname)
            if m is None or None in (r0, r1, c0, c1):
                continue
            self._tables.append(
                (r0, r1, c0, c1, int(m.group(1))))  # type: ignore[arg-type]
        self._events: dict[int, SeasonalEvent] = {}

    def _event(self, session: Session, year: int) -> SeasonalEvent:
        ev = self._events.get(year)
        if ev is None:
            ev = session.execute(
                select(SeasonalEvent).where(
                    SeasonalEvent.household_id == self.household_id,
                    SeasonalEvent.year == year,
                    SeasonalEvent.type == "christmas")
            ).scalar_one_or_none()
            if ev is None:
                ev = SeasonalEvent(
                    household_id=self.household_id, year=year,
                    type="christmas", name=f"Christmas {year}",
                    budget_pence=0)
                session.add(ev)
                session.flush()
            self._events[year] = ev
        return ev

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        m = _ROW_RE.search(record.cell_range)
        row = int(m.group(1)) if m else 0
        vals = record_values(record)
        target: tuple[str, int] | None = None
        for r0, r1, c0, _c1, year in self._tables:
            if not r0 <= row <= r1:
                continue
            who = vals.get(c0)
            what = vals.get(c0 + 1)
            how = _num(vals.get(c0 + 2))
            when = vals.get(c0 + 3)
            if (not isinstance(who, str) or not who.strip()
                    or who.strip().lower().startswith("who")
                    or how is None):
                continue
            gift = Gift(
                event_id=self._event(session, year).id,
                recipient=who.strip(), idea=str(what or ""),
                planned_pence=round(how * 100),
                purchase_status="idea", payment_status="unpaid",
                notes=str(when or ""))
            session.add(gift)
            ev = self._events[year]
            ev.budget_pence = (
                sum(g.planned_pence for g in ev.gifts)
                + gift.planned_pence)
            session.flush()
            target = target or ("gift", gift.id)
        return target


class SlowBoatExtractor:
    """slowBoatIncomeTbl rows → historical income Transactions under an
    archived category — reporting only, no active workflow. Amounts are
    literal tips + cached delivery charges; the 'Daily Total' formula
    column is never trusted (defect A)."""

    CATEGORY = "Slow Boat (archived)"

    def __init__(self, sheet: str, household_id: int, account_id: int,
                 cache: dict[tuple[str, int, int], Any]) -> None:
        self.sheet = sheet
        self.household_id = household_id
        self.account_id = account_id
        self.cache = cache
        self._cat: Category | None = None

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        vals = record_values(record)
        work = _as_date(vals.get(2))
        tips = _num(vals.get(5)) or 0.0
        if work is None:
            return None
        m = _ROW_RE.search(record.cell_range)
        row = int(m.group(1)) if m else 0
        delivery = _cached_num(self.cache, self.sheet, row, 4) or 0.0
        amount = round((tips + delivery) * 100)
        if amount <= 0:
            return None
        if self._cat is None:
            self._cat = session.execute(
                select(Category).where(
                    Category.household_id == self.household_id,
                    Category.name == self.CATEGORY)
            ).scalar_one_or_none() or Category(
                household_id=self.household_id, name=self.CATEGORY)
            if self._cat.id is None:
                session.add(self._cat)
                session.flush()
        t = Transaction(
            account_id=self.account_id, date=work,
            amount_pence=amount, payee="Slow Boat delivery",
            status=TransactionStatus.cleared,
            source=TransactionSource.excel_import,
            external_ref=f"imp:{batch.id}:{record.id}",
            import_record_id=record.id)
        session.add(t)
        session.flush()
        return "transaction", t.id
