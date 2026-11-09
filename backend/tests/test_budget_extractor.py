"""Budget sheet extractor (issue #46): annual + member2 budget rows →
monthly Budget/BudgetLine entities with provenance, scoping and
independent reconciliation — workbook totals are never trusted."""

from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.migration.extractors import BudgetYearExtractor
from app.migration.pipeline import commit_batch, create_batch
from app.persistence.models import (
    Budget,
    BudgetLine,
    BudgetLineSide,
    Category,
    Household,
    ImportRecord,
    Role,
    UserProfile,
)

YEAR_TABLES = {
    "tblIncome2025": "B5:P8",
    "tblPersonal2025": "B14:P16",
    "tblShared2025": "B20:P22",
    "tblTotals2025": "B25:P26",
}

MEMBER2_TABLES = {
    "tblIncome7": "B5:P7",
    "tblDaily8": "B10:P12",
    "tblOther9": "B14:P16",
    "tblTotals10": "B18:P19",
}


def _row(ws, r: int, label: str | None, months: list[float | str],
         total_formula: bool = True) -> None:
    if label is not None:
        ws.cell(row=r, column=2, value=label)
    for i, v in enumerate(months):
        ws.cell(row=r, column=3 + i, value=v)
    if total_formula:
        ws.cell(row=r, column=15, value=f"=SUM(C{r}:N{r})")


def _budget_workbook() -> Workbook:
    """Signature-faithful synthetic annual sheet + member sheet —
    header rows, literal month cells, formula subtotals (structure only;
    values are fictional)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget-2025"
    ws["A1"] = 2025  # title cell
    # income section (rows 5-8: header, 2 data, subtotal)
    _row(ws, 5, "INCOME", ["JAN"] * 12, total_formula=False)
    _row(ws, 6, "Salary", [2500.0] * 12)
    _row(ws, 7, "Side gig", [50.0, 50.0] + [None] * 10)  # sparse months
    _row(ws, 8, "Total", ["=SUM(C6:C7)"] + [None] * 11,
         total_formula=False)
    # personal section (rows 14-16)
    _row(ws, 14, "PERSONAL BILLS", ["JAN"] * 12, total_formula=False)
    _row(ws, 15, "Fuel", [100.0] * 12)
    _row(ws, 16, "Total", ["=C15"] + [None] * 11, total_formula=False)
    # shared section (rows 20-22)
    _row(ws, 20, "SHARED BILLS", ["JAN"] * 12, total_formula=False)
    _row(ws, 21, "Groceries", [139.19] * 12)
    _row(ws, 22, "Total", ["=C21"] + [None] * 11, total_formula=False)
    # totals section (rows 25-26) — must never produce lines
    _row(ws, 25, "TOTALS", ["JAN"] * 12, total_formula=False)
    _row(ws, 26, "Grand total", ["=C8+C16+C22"] + [None] * 11,
         total_formula=False)

    ws2 = wb.create_sheet("Budget-Partner")
    _row(ws2, 5, "INCOME", ["JAN"] * 12, total_formula=False)
    _row(ws2, 6, "Partner salary", [1800.0] * 12)
    _row(ws2, 7, "Total", ["=C6"] + [None] * 11, total_formula=False)
    _row(ws2, 10, "BILLS", ["JAN"] * 12, total_formula=False)
    _row(ws2, 11, "Personal phone", [25.50] * 12)
    _row(ws2, 12, "Total", ["=C11"] + [None] * 11, total_formula=False)
    _row(ws2, 14, "OTHER MONEY MOVES", ["JAN"] * 12, total_formula=False)
    _row(ws2, 15, "Savings transfer", [200.0] * 12)
    _row(ws2, 16, "Total", ["=C15"] + [None] * 11, total_formula=False)
    _row(ws2, 18, "TOTALS", ["JAN"] * 12, total_formula=False)
    _row(ws2, 19, "Grand total", ["=C7+C12+C16"] + [None] * 11,
         total_formula=False)
    return wb


@pytest.fixture()
def wb_path(tmp_path: Path) -> Path:
    p = tmp_path / "budgets.xlsx"
    _budget_workbook().save(p)
    return p


@pytest.fixture()
def owner(session: Session, household: Household) -> UserProfile:
    p = UserProfile(household_id=household.id, display_name="Owner",
                    role=Role.admin)
    session.add(p)
    session.flush()
    return p


@pytest.fixture()
def member2(session: Session, household: Household) -> UserProfile:
    p = UserProfile(household_id=household.id, display_name="Partner",
                    role=Role.member)
    session.add(p)
    session.flush()
    return p


def _year_extractor(household: Household, owner: UserProfile):
    return BudgetYearExtractor(
        "Budget-2025", household_id=household.id,
        owner_profile_id=owner.id, year=2025, name="Annual",
        tables=YEAR_TABLES)


def _member_extractor(household: Household, member2: UserProfile):
    return BudgetYearExtractor(
        "Budget-Partner", household_id=household.id,
        owner_profile_id=member2.id, year=2025, name="Partner",
        tables=MEMBER2_TABLES, all_owner=True)


def test_formula_month_cells_resolve_via_cache(
        session: Session, household: Household, owner: UserProfile,
        wb_path: Path):
    """#388 — a mid-year edit stored as a formula still produces its
    month's line, resolved through the cached-values map."""
    ex = BudgetYearExtractor(
        "Budget-2025", household_id=household.id,
        owner_profile_id=owner.id, year=2025, name="Annual",
        tables=YEAR_TABLES,
        cache={("Budget-2025", 15, 5): 67.13})
    batch, _ = create_batch(session, household.id, wb_path, "tester")
    # Row 15 (tblPersonal2025): MAR is a formula cell — literal
    # months map, the formula month resolves from the cache.
    rec = ImportRecord(
        batch_id=batch.id, sheet="Budget-2025",
        cell_range="Budget-2025!B15:O15",
        interpreted_value='[[2, "Phone"], [3, 20.0], [5, "=C15+47.13"],'
                          ' [6, 67.13]]',
        classification="actual")
    out = ex.map_record(session, batch, rec)
    assert out is not None and out[0] == "budget_line"
    lines = session.execute(
        select(BudgetLine).join(Budget)).scalars().all()
    by_month = {ln.budget.start_date.month: ln.original_pence
                for ln in lines}
    assert by_month[1] == 2000
    assert by_month[3] == 6713  # formula month resolved, not dropped
    assert by_month[4] == 6713


def _lines(session: Session, budget: Budget) -> list[BudgetLine]:
    return list(session.execute(
        select(BudgetLine).where(BudgetLine.budget_id == budget.id)
    ).scalars().all())


class TestYearSheet:
    def test_monthly_budgets_and_scopes(
        self, session: Session, household: Household,
        owner: UserProfile, wb_path: Path,
    ) -> None:
        batch, _ = create_batch(session, household.id, wb_path, "tester")
        commit_batch(session, batch, [_year_extractor(household, owner)])
        session.commit()
        budgets = list(session.execute(
            select(Budget).where(Budget.household_id == household.id)
        ).scalars().all())
        # 12 months × (personal scope + shared scope)
        assert len(budgets) == 24
        personal = [b for b in budgets
                    if b.owner_profile_id == owner.id]
        shared = [b for b in budgets if b.owner_profile_id is None]
        assert len(personal) == len(shared) == 12
        assert all(b.period_type == "monthly" for b in budgets)

    def test_line_values_and_sides(
        self, session: Session, household: Household,
        owner: UserProfile, wb_path: Path,
    ) -> None:
        batch, _ = create_batch(session, household.id, wb_path, "tester")
        commit_batch(session, batch, [_year_extractor(household, owner)])
        session.commit()
        feb_personal = session.execute(
            select(Budget).where(
                Budget.name == "Annual 2025-02 (personal)")
        ).scalar_one()
        lines = {line.category_id: line for line in _lines(session, feb_personal)}
        cats = {c.id: c.name for c in session.execute(
            select(Category)).scalars().all()}
        by_name = {cats[cid]: line for cid, line in lines.items()}
        assert by_name["Fuel"].original_pence == 10000
        assert by_name["Salary"].original_pence == 250000
        assert by_name["Salary"].side == BudgetLineSide.income
        assert by_name["Side gig"].original_pence == 5000
        # sparse row: no Side gig line beyond Feb
        mar_personal = session.execute(
            select(Budget).where(
                Budget.name == "Annual 2025-03 (personal)")
        ).scalar_one()
        assert {cats[line.category_id]
                for line in _lines(session, mar_personal)} == {
                    "Fuel", "Salary"}
        # pence conversion of a decimal £ value, shared scope
        jan_shared = session.execute(
            select(Budget).where(Budget.name == "Annual 2025-01 (shared)")
        ).scalar_one()
        (grocery,) = _lines(session, jan_shared)
        assert grocery.original_pence == 13919
        assert grocery.side == BudgetLineSide.expense

    def test_independent_reconciliation(
        self, session: Session, household: Household,
        owner: UserProfile, wb_path: Path,
    ) -> None:
        """Imported totals must equal independently recomputed cell
        sums — never the workbook's own subtotal formulas (defect A)."""
        batch, _ = create_batch(session, household.id, wb_path, "tester")
        commit_batch(session, batch, [_year_extractor(household, owner)])
        session.commit()
        # independent recompute straight from the fixture spec
        expected = {
            "Annual 2025-01 (personal)": 250000 + 5000 + 10000,
            "Annual 2025-03 (personal)": 250000 + 10000,
            "Annual 2025-06 (shared)": 13919,
        }
        for name, total in expected.items():
            b = session.execute(
                select(Budget).where(Budget.name == name)).scalar_one()
            assert sum(line.original_pence
                       for line in _lines(session, b)) == total

    def test_aggregates_never_lines_and_provenance(
        self, session: Session, household: Household,
        owner: UserProfile, wb_path: Path,
    ) -> None:
        batch, _ = create_batch(session, household.id, wb_path, "tester")
        commit_batch(session, batch, [_year_extractor(household, owner)])
        session.commit()
        names = [c.name for c in session.execute(
            select(Category)).scalars().all()]
        assert "Total" not in names and "Grand total" not in names
        # every targeted record maps to a real line; untouched records
        # (headers/aggregates) carry no target
        targets = session.execute(
            select(ImportRecord.target_entity, ImportRecord.target_id)
            .where(ImportRecord.batch_id == batch.id,
                   ImportRecord.target_entity.is_not(None))).all()
        assert targets and all(t == "budget_line" for t, _ in targets)
        line_ids = {lid for _, lid in targets}
        assert session.execute(
            select(func.count(BudgetLine.id)).where(
                BudgetLine.id.in_(line_ids))).scalar_one() == len(
                    line_ids)


class TestMemberSheet:
    def test_all_sections_owner_scoped(
        self, session: Session, household: Household,
        member2: UserProfile, wb_path: Path,
    ) -> None:
        batch, _ = create_batch(session, household.id, wb_path, "tester")
        commit_batch(session, batch,
                     [_member_extractor(household, member2)])
        session.commit()
        budgets = list(session.execute(
            select(Budget).where(Budget.household_id == household.id)
        ).scalars().all())
        # 12 months, all private to member2 — zero shared budgets
        assert len(budgets) == 12
        assert all(b.owner_profile_id == member2.id for b in budgets)
        jan = session.execute(
            select(Budget).where(Budget.name == "Partner 2025-01 (personal)")
        ).scalar_one()
        cats = {c.id: c.name for c in session.execute(
            select(Category)).scalars().all()}
        by_name = {cats[line.category_id]: line
                   for line in _lines(session, jan)}
        assert by_name["Partner salary"].side == BudgetLineSide.income
        assert by_name["Personal phone"].original_pence == 2550
        assert by_name["Savings transfer"].original_pence == 20000


class TestIdempotency:
    def test_reimport_updates_not_duplicates(
        self, session: Session, household: Household,
        owner: UserProfile, wb_path: Path,
    ) -> None:
        b1, _ = create_batch(session, household.id, wb_path, "tester")
        commit_batch(session, b1, [_year_extractor(household, owner)])
        session.commit()
        n_budgets = session.execute(
            select(func.count(Budget.id))).scalar_one()
        n_lines = session.execute(
            select(func.count(BudgetLine.id))).scalar_one()
        # second commit of the same batch is a pipeline no-op
        assert commit_batch(
            session, b1, [_year_extractor(household, owner)]) == 0
        session.commit()
        assert session.execute(
            select(func.count(Budget.id))).scalar_one() == n_budgets
        assert session.execute(
            select(func.count(BudgetLine.id))).scalar_one() == n_lines
