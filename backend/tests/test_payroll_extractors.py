"""Payroll-domain extractors (issue #47): salary history, overtime,
monthly payroll model and tax reference tables. The synthetic sheets
mirror the real workbook's column layouts; formula cells the real file
computes are supplied via the ``cache`` map (Excel's saved results)."""

from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.migration.extractors import (
    OvertimeExtractor,
    PayrollExtractor,
    SalaryHistoryExtractor,
    TaxTablesExtractor,
    _pay_date,
)
from app.migration.pipeline import commit_batch, create_batch
from app.persistence.models import (
    Employment,
    Household,
    OvertimeEntry,
    PayrollRun,
    PayrollRunStatus,
    Role,
    SalaryRevision,
    TaxRuleSet,
    UserProfile,
)

TAX_TABLES = {"tblPAYE_Bands": "B4:F8", "tblNI_Bands": "H4:M6",
              "tblStudentLoanPlans": "B12:E14"}


def _payroll_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Salary History"
    _row(ws, 9, None, ["Effective Date", "Annual Salary", "Pension %",
                       "Pension Type", "Student Loan Plan",
                       "Postgrad Loan Plan", "NI Category",
                       "Weekly Hours"])
    _row(ws, 10, None, ["2024-04-06 00:00:00", 30000, 5, "net_pay",
                        "plan2", "none", "A", 37.5])
    _row(ws, 11, None, ["2025-04-06 00:00:00", 33000, 5, "net_pay",
                        "plan2", "none", "A", 37.5])

    ws = wb.create_sheet("Overtime")
    _row(ws, 5, None, ["Date", "Payroll Date", "Actual date", "Hours",
                       "Multiplier", "Hourly Rate", "Overtime Rate",
                       "Overtime Pay"])
    # Sat 2025-01-11 (before the 15th → paid 28 Jan); Wed 2025-02-19
    # (after the 15th → paid 28 Mar — defect F boundary)
    _row(ws, 6, None, ["2025-01-11 00:00:00", "=f", None, 4, "=f", "=f",
                       "=f", "=f"])
    _row(ws, 7, None, ["2025-02-19 00:00:00", "=f", "2025-02-19", 6,
                       "=f", "=f", "=f", "=f"])

    ws = wb.create_sheet("Monthly Model")
    _row(ws, 3, None, ["Month", "Tax Year", "Tax Year Month Number",
                       "Tax Code"] + [f"c{i}" for i in range(5, 15)])
    for i, m in enumerate(("2025-01-01", "2025-02-01")):
        r = 4 + i
        vals = [f"{m} 00:00:00"] + ["=f"] * 25
        vals[3] = "1257L"  # tax code literal (col 5)
        _row(ws, r, None, vals)

    ws = wb.create_sheet("Tax Tables")
    _row(ws, 4, None, ["Tax Year", "Band Order", "Lower Limit",
                       "Upper Limit", "Rate"])
    _row(ws, 5, None, ["2024/25", 1, 0, 12570, 0])
    _row(ws, 6, None, ["2024/25", 2, 12570, 50270, 0.2])
    _row(ws, 5, "x", ["2024/25", "A", 1, 0, 12570, 0.12], offset=7)
    _row(ws, 12, None, ["Tax Year", "Plan", "Threshold", "Rate"])
    _row(ws, 13, None, ["2024/25", "plan2", 27295, 0.09])
    return wb


def _row(ws, r, label, values, offset=1):
    if label is not None:
        ws.cell(row=r, column=1, value=label)
    for i, v in enumerate(values):
        ws.cell(row=r, column=offset + 1 + i, value=v)


@pytest.fixture()
def wb_path(tmp_path: Path) -> Path:
    p = tmp_path / "payroll.xlsx"
    _payroll_workbook().save(p)
    return p


@pytest.fixture()
def employment(session: Session, household: Household) -> Employment:
    p = UserProfile(household_id=household.id, display_name="Owner",
                    role=Role.admin)
    session.add(p)
    session.flush()
    e = Employment(household_id=household.id, profile_id=p.id,
                   employer="ACME", jurisdiction="england_wales",
                   ni_category="A", student_loan_plan="plan2",
                   pension_mode="net_pay", pension_employee_bps=500,
                   pension_fixed_pence=0, contracted_hours=37.5,
                   payday_rule="last_friday")
    session.add(e)
    session.flush()
    return e


# cached formula results the real file would hold
OT_CACHE = {("Overtime", 6, 6): 1.5, ("Overtime", 6, 7): 17.30,
            ("Overtime", 7, 6): 2.0, ("Overtime", 7, 7): 17.30}
MM_CACHE = {("Monthly Model", 4, 3): "2024/25",
            ("Monthly Model", 4, 17): 2600.0,
            ("Monthly Model", 4, 27): 2050.0,
            ("Monthly Model", 4, 22): 200.0,
            ("Monthly Model", 4, 24): 120.0,
            ("Monthly Model", 4, 16): 150.0,
            ("Monthly Model", 5, 3): "2024/25",
            ("Monthly Model", 5, 17): 2450.0,
            ("Monthly Model", 5, 27): 2100.0}


class TestSalaryHistory:
    def test_revisions_import(
        self, session: Session, household: Household,
        employment: Employment, wb_path: Path,
    ) -> None:
        batch, _ = create_batch(session, household.id, wb_path, "t")
        commit_batch(session, batch,
                     [SalaryHistoryExtractor("Salary History",
                                             employment.id)])
        session.commit()
        revs = list(session.execute(
            select(SalaryRevision)
            .where(SalaryRevision.employment_id == employment.id)
            .order_by(SalaryRevision.effective_date)).scalars())
        assert [r.effective_date for r in revs] == [
            date(2024, 4, 6), date(2025, 4, 6)]
        assert [r.annual_salary_pence for r in revs] == [
            3000000, 3300000]
        assert '"student_loan_plan": "plan2"' in (revs[0].notes or "")


class TestOvertime:
    def test_entries_and_period_attribution(
        self, session: Session, household: Household,
        employment: Employment, wb_path: Path,
    ) -> None:
        batch, _ = create_batch(session, household.id, wb_path, "t")
        commit_batch(session, batch, [
            OvertimeExtractor("Overtime", employment.id, OT_CACHE,
                              today=date(2025, 3, 1))])
        session.commit()
        entries = list(session.execute(
            select(OvertimeEntry)
            .order_by(OvertimeEntry.work_date)).scalars())
        assert len(entries) == 2
        jan, feb = entries
        assert jan.day_type == "saturday"
        assert jan.multiplier_bps == 15000
        assert jan.hourly_rate_pence == 1730
        # worked 11 Jan (<15th) → paid 28 Jan; 19 Feb (≥15th) → 28 Mar
        assert '"payroll_date": "2025-01-28"' in (jan.notes or "")
        assert '"payroll_date": "2025-03-28"' in (feb.notes or "")
        assert feb.multiplier_bps == 20000


class TestPayroll:
    def test_runs_and_components(
        self, session: Session, household: Household,
        employment: Employment, wb_path: Path,
    ) -> None:
        batch, _ = create_batch(session, household.id, wb_path, "t")
        commit_batch(session, batch, [
            PayrollExtractor("Monthly Model", employment.id,
                             "england_wales", MM_CACHE,
                             today=date(2025, 3, 1))])
        session.commit()
        runs = list(session.execute(
            select(PayrollRun).order_by(PayrollRun.period_start)
        ).scalars())
        assert len(runs) == 2
        jan, feb = runs
        assert jan.status == PayrollRunStatus.reconciled  # paid 28 Jan
        assert feb.status == PayrollRunStatus.reconciled  # paid 28 Feb
        assert jan.gross_pence == 260000
        assert jan.net_pence == 205000
        kinds = {c.kind for c in jan.components}
        assert {"overtime", "tax", "ni"} <= kinds
        tax = next(c for c in jan.components if c.kind == "tax")
        assert tax.actual_pence == 20000


class TestTaxTables:
    def test_versioned_rule_sets_skip_formulas(
        self, session: Session, household: Household,
        wb_path: Path,
    ) -> None:
        batch, _ = create_batch(session, household.id, wb_path, "t")
        commit_batch(session, batch, [
            TaxTablesExtractor("Tax Tables", "england_wales",
                               TAX_TABLES)])
        session.commit()
        rs = session.execute(
            select(TaxRuleSet).where(TaxRuleSet.tax_year == "2024/25")
        ).scalar_one()
        import json as j
        params = j.loads(rs.params)
        assert len(params["paye"]) == 2
        assert len(params["ni"]) == 1
        assert params["student_loan"][0]["plan"] == "plan2"


class TestPayDateRule:
    @pytest.mark.parametrize("work,expected", [
        (date(2025, 1, 10), date(2025, 1, 28)),   # before 15th → Jan 28
        (date(2025, 1, 15), date(2025, 2, 28)),   # 15th+ → Feb 28
        (date(2025, 12, 20), date(2026, 1, 28)),  # year rollover
        (date(2025, 6, 1), date(2025, 6, 27)),    # Jun 28 = Sat → Fri
    ])
    def test_rule(self, work: date, expected: date) -> None:
        assert _pay_date(work) == expected
