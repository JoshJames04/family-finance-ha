"""Synthetic workbook generator (issue #44).

Builds a deterministic .xlsx mimicking the real workbook's *structure* —
sheet names, hidden sheets, formula patterns and the six catalogued
defects (A–F) — with purely fictional values. Structure is not
sensitive; values are. See tests/fixtures/README.md policy.

Determinism: no RNG, no clock. Byte-identical output across runs is not
guaranteed by openpyxl (zips embed timestamps), but cell-for-cell
equality is — tests assert structure, not bytes.
"""

from __future__ import annotations

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

# Fictional entities — deliberately unreal per fixture policy
FICTION = {
    "employer": "ACME Manufacturing Ltd",
    "member_a": "Bob Fable",
    "member_b": "Sam Story",
    "energy": "Contoso Energy",
    "mortgage": "Fictional Building Society",
    "grocer": "Unreal Foods",
    "lender": "Mum & Dad (fictional loan)",
}

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _sheet(wb: Workbook, name: str, hidden: bool = False) -> Worksheet:
    ws = wb.create_sheet(name)
    if hidden:
        ws.sheet_state = "hidden"
    return ws


def _summary(wb: Workbook) -> None:
    ws = _sheet(wb, "Summary")
    ws["A1"] = "Family Budget — Summary"
    ws["A3"], ws["B3"] = "Metric", "Value"
    for i, (k, v) in enumerate([
            ("Household", "Fable/Story household"),
            ("Currency", "GBP"),
            ("Workbook generated", "fixture — synthetic")]):
        ws.cell(row=4 + i, column=1, value=k)
        ws.cell(row=4 + i, column=2, value=v)


def _income(wb: Workbook) -> None:
    ws = _sheet(wb, "Income")
    ws.append(["Month", "Employer", "Gross", "Net"])
    for i, m in enumerate(MONTHS):
        r = 2 + i
        ws.cell(row=r, column=1, value=m)
        ws.cell(row=r, column=2, value=FICTION["employer"])
        ws.cell(row=r, column=3, value=300000)  # £3,000 gross
        ws.cell(row=r, column=4, value=f"=C{r}*0.76")


def _bills_defect_a(wb: Workbook) -> None:
    """Defect A — annual total includes intermediate monthly sums.

    Rows 4-15 are monthly amounts; rows 16-27 are monthly *subtotals*
    that already include the monthly figures. The 'annual' formula
    naively sums BOTH blocks → inflates the true total 2x.
    """
    ws = _sheet(wb, "Bills-2026")
    ws["A1"] = "House bills 2026"
    ws["A2"], ws["B2"] = "Month", "Amount"
    for i, m in enumerate(MONTHS):
        ws.cell(row=3 + i, column=1, value=m)
        ws.cell(row=3 + i, column=2, value=12000 + (i % 3) * 500)
    for i in range(12):
        r = 15 + i
        ws.cell(row=r, column=1, value=f"{MONTHS[i]} subtotal")
        ws.cell(row=r, column=2, value=f"=B{3 + i}")
    ws["A28"], ws["B28"] = "ANNUAL TOTAL", "=SUM(B3:B26)"  # defect
    ws["A29"], ws["B29"] = "TRUE ANNUAL", "=SUM(B3:B14)"


def _loan_defect_b(wb: Workbook) -> None:
    """Defect B — broken #REF! in remaining-balance column."""
    ws = _sheet(wb, "Family-Loan")
    ws.append(["Period", "Payment", "Interest", "Remaining"])
    balance = 2500000
    for i in range(12):
        r = 2 + i
        ws.cell(row=r, column=1, value=i + 1)
        ws.cell(row=r, column=2, value=-50000)
        ws.cell(row=r, column=3, value=5200)
        if i < 5:
            ws.cell(row=r, column=4, value=balance - (i + 1) * 50000)
        else:
            ws.cell(row=r, column=4, value="=#REF!+0")  # defect


def _budget_defects_ce(wb: Workbook) -> None:
    """Defects C + E — stale annual total; forecast cell overwritten."""
    ws = _sheet(wb, "Budget-2024")  # older sheet → defect C
    ws.append(["Month", "Spend", "Annual"])
    for i, m in enumerate(MONTHS):
        ws.cell(row=2 + i, column=1, value=m)
        ws.cell(row=2 + i, column=2, value=180000 + i * 1000)
    # defect C: literal 2400000 != SUM(B2:B13) (true ~2183000)
    ws["B14"] = 2400000

    ws2 = _sheet(wb, "Budget-2027")  # forecast sheet → defect E
    ws2.append(["Month", "Forecast", "Notes"])
    for i, m in enumerate(MONTHS):
        r = 2 + i
        ws2.cell(row=r, column=1, value=m)
        # defect E: forecast formula for most months, but Apr–Jun were
        # overwritten by literal actuals — forecast history lost
        ws2.cell(row=r, column=2,
                 value=198500 + i * 200 if 3 <= i <= 5 else "=200000")
        if 3 <= i <= 5:
            ws2.cell(row=r, column=3, value="actual overwritten")


def _payroll_defects_df(wb: Workbook) -> None:
    """Defects D + F — hardcoded PAYE/NI constants; out-of-range period."""
    ws = _sheet(wb, "Payroll")
    ws.append(["Month", "Gross", "PAYE", "NI", "Net"])
    for i, m in enumerate(MONTHS):
        r = 2 + i
        gross = 300000 + (20000 if i % 4 == 3 else 0)  # occasional OT
        ws.cell(row=r, column=1, value=m)
        ws.cell(row=r, column=2, value=gross)
        ws.cell(row=r, column=3, value=f"=B{r}*0.2")      # defect D
        ws.cell(row=r, column=4, value=f"=B{r}*0.08")     # defect D
        ws.cell(row=r, column=5, value=f"=B{r}-C{r}-D{r}")
    # defect F: summary refs period 14 of a 12-period layout
    ws["A15"] = "Annual PAYE"
    ws["B15"] = "=C2+C3+C4+C5+C6+C7+C8+C9+C10+C11+C12+C13+'Payroll-Old'!C14"
    ws["A16"] = "Period 14 check"
    ws["B16"] = "='Payroll-Old'!B14"


def _transfers_savings(wb: Workbook) -> None:
    ws = _sheet(wb, "Transfers")
    ws.append(["Date", "From", "To", "Amount"])
    for i in range(4):
        r = 2 + i
        ws.cell(row=r, column=1, value=f"2026-0{i + 1}-05")
        ws.cell(row=r, column=2, value="Joint")
        ws.cell(row=r, column=3, value="Bills")
        ws.cell(row=r, column=4, value=250000)

    ws2 = _sheet(wb, "Savings")
    ws2.append(["Month", "Deposit", "Balance"])
    for i, m in enumerate(MONTHS):
        r = 2 + i
        ws2.cell(row=r, column=1, value=m)
        ws2.cell(row=r, column=2, value=20000)
        ws2.cell(row=r, column=3, value=f"=SUM($B$2:B{r})")


def _christmas(wb: Workbook) -> None:
    ws = _sheet(wb, "Christmas")
    ws.append(["Month", "Saved", "Target"])
    for i, m in enumerate(MONTHS):
        ws.cell(row=2 + i, column=1, value=m)
        ws.cell(row=2 + i, column=2, value=25000)
        ws.cell(row=2 + i, column=3, value=300000)


def _hidden_sheets(wb: Workbook) -> None:
    for name, rows in [
        ("Tax-Tables", [("band", "rate"), ("basic", 0.2), ("higher", 0.4),
                        ("ni", 0.08)]),
        ("Lookups", [("category", "budget"), ("Groceries", 40000),
                     ("Energy", 15000)]),
        ("Calc", [("intermediate", "=1+1")]),
        ("Payroll-Old", [("Month", "Gross")] +
            [(m, 290000) for m in MONTHS] +
            [("P13", 300000), ("P14", 310000)]),  # defect F anchor
        ("Archive-2023", [("archived", True)]),
        ("Scratch", [("wip", None)]),
    ]:
        ws = _sheet(wb, name, hidden=True)
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                if v is not None:
                    ws.cell(row=1 + i, column=1 + j, value=v)


def build_workbook() -> Workbook:
    """The synthetic Family Budget.xlsx — structure only, fictional data."""
    wb = Workbook()
    wb.remove(wb.active)
    _summary(wb)
    _income(wb)
    _bills_defect_a(wb)
    _loan_defect_b(wb)
    _budget_defects_ce(wb)
    _payroll_defects_df(wb)
    _transfers_savings(wb)
    _christmas(wb)
    _hidden_sheets(wb)
    return wb


SHEET_NAMES = [
    "Summary", "Income", "Bills-2026", "Family-Loan", "Budget-2024",
    "Budget-2027", "Payroll", "Transfers", "Savings", "Christmas",
    "Tax-Tables", "Lookups", "Calc", "Payroll-Old", "Archive-2023",
    "Scratch",
]
HIDDEN = {"Tax-Tables", "Lookups", "Calc", "Payroll-Old",
          "Archive-2023", "Scratch"}


if __name__ == "__main__":  # pragma: no cover - manual fixture generation
    import sys
    build_workbook().save(sys.argv[1] if len(sys.argv) > 1
                          else "synthetic_workbook.xlsx")
