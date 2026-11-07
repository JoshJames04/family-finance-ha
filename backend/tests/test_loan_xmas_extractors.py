"""Loan ledger, Christmas and Piecework Co extractors (issue #48):
balance reconstruction without trusting the workbook's broken
'Remaining' formula (defect B), seasonal gifts, and archived
reporting-only income."""

from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.migration.extractors import (
    LoanLedgerExtractor,
    PieceworkExtractor,
    XmasExtractor,
)
from app.migration.pipeline import commit_batch, create_batch
from app.persistence.models import (
    Account,
    AccountType,
    Gift,
    Household,
    Loan,
    SeasonalEvent,
    Transaction,
)

XMAS_TABLES = {"xmas2025": "B5:E8", "xmas2026": "H5:K7"}
SB_CACHE = {("Piecework Co", 7, 4): 21.40, ("Piecework Co", 8, 4): 15.00}


def _wb() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Loan Ledger"
    ws.cell(row=4, column=2, value="Total Amount")
    ws.cell(row=4, column=3, value=9600)
    ws.cell(row=10, column=2, value="Year")
    ws.cell(row=10, column=3, value="Amount")
    ws.cell(row=10, column=4, value="Remaining")
    for i, (y, amt) in enumerate([(2019, 1200), (2020, 0),
                                  (2021, 800)]):
        r = 11 + i
        ws.cell(row=r, column=2, value=y)
        ws.cell(row=r, column=3, value=amt)
        ws.cell(row=r, column=4, value="=BROKEN()")  # defect B

    ws = wb.create_sheet("Xmas")
    for i, who in enumerate(["Who?", "Gran", "Dad"]):
        r = 5 + i
        ws.cell(row=r, column=2, value=who)
        ws.cell(row=r, column=3, value="What?")
        ws.cell(row=r, column=4, value=120 if r > 5 else "How much?")
        ws.cell(row=r, column=5, value="Dec" if r > 5 else "When?")
    ws.cell(row=5, column=8, value="Who?")
    ws.cell(row=6, column=8, value="Aunt")
    ws.cell(row=6, column=9, value="Book")
    ws.cell(row=6, column=10, value=25)
    ws.cell(row=6, column=11, value="Nov")

    ws = wb.create_sheet("Piecework Co")
    ws.cell(row=6, column=2, value="Date")
    for i, (d, tips) in enumerate([("2024-03-02", 12), ("2024-03-03", 8)]):
        r = 7 + i
        ws.cell(row=r, column=2, value=f"{d} 00:00:00")
        ws.cell(row=r, column=3, value=5)
        ws.cell(row=r, column=4, value="=formula")
        ws.cell(row=r, column=5, value=tips)
    return wb


@pytest.fixture()
def wb_path(tmp_path: Path) -> Path:
    p = tmp_path / "e48.xlsx"
    _wb().save(p)
    return p


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Import",
                type=AccountType.personal, currency="GBP",
                opening_balance_pence=0,
                opening_date=date(2024, 1, 1))
    session.add(a)
    session.flush()
    return a


def test_loan_reconstruction(
    session: Session, household: Household, wb_path: Path,
) -> None:
    batch, _ = create_batch(session, household.id, wb_path, "t")
    commit_batch(session, batch, [
        LoanLedgerExtractor("Loan Ledger", household.id, "Family")])
    session.commit()
    loan = session.execute(select(Loan)).scalar_one()
    assert loan.principal_pence == 960000
    paid = sum(r.amount_pence for r in loan.repayments)
    assert paid == 200000  # 1200+800; the £0 year is not a repayment
    # defect B: residual reconstructed, never read from =BROKEN() cells
    assert loan.principal_pence - paid == 760000
    assert "reconstructed" in (loan.notes or "")


def test_xmas_events_and_gifts(
    session: Session, household: Household, wb_path: Path,
) -> None:
    batch, _ = create_batch(session, household.id, wb_path, "t")
    commit_batch(session, batch, [
        XmasExtractor("Xmas", household.id, XMAS_TABLES)])
    session.commit()
    events = {e.year: e for e in session.execute(
        select(SeasonalEvent)).scalars()}
    assert set(events) == {2025, 2026}
    gifts = list(session.execute(select(Gift)).scalars())
    assert len(gifts) == 3
    assert sum(g.planned_pence for g in gifts) == 26500
    assert events[2025].budget_pence == 24000


def test_piecework_archived_income(
    session: Session, household: Household, account: Account,
    wb_path: Path,
) -> None:
    batch, _ = create_batch(session, household.id, wb_path, "t")
    commit_batch(session, batch, [
        PieceworkExtractor("Piecework Co", household.id, account.id,
                          SB_CACHE)])
    session.commit()
    txns = list(session.execute(select(Transaction)).scalars())
    assert len(txns) == 2
    assert txns[0].amount_pence == 3340   # 21.40 + 12.00 tips
    assert txns[0].external_ref is not None
    assert all(t.import_record_id is not None for t in txns)
