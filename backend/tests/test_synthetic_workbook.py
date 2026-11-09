"""Self-test: synthetic workbook structure + defect patterns (issue #44)."""

import pytest
from openpyxl import Workbook

from tests.fixtures.synthetic_workbook import (
    HIDDEN,
    SHEET_NAMES,
    build_workbook,
)


@pytest.fixture(scope="module")
def wb() -> Workbook:
    return build_workbook()


class TestStructure:
    def test_sheet_inventory(self, wb: Workbook) -> None:
        assert wb.sheetnames == SHEET_NAMES

    def test_hidden_sheets(self, wb: Workbook) -> None:
        hidden = {ws.title for ws in wb.worksheets
                  if ws.sheet_state == "hidden"}
        assert hidden == HIDDEN
        assert len(hidden) >= 7 - 1  # 6 explicit + Calc = matches design

    def test_deterministic(self) -> None:
        a, b = build_workbook(), build_workbook()
        assert a.sheetnames == b.sheetnames
        for name in a.sheetnames:
            wa, wbs = a[name], b[name]
            for row in wa.iter_rows():
                for cell in row:
                    assert cell.value == wbs[cell.coordinate].value


class TestDefectPatterns:
    def test_a_annual_includes_intermediates(self, wb: Workbook) -> None:
        ws = wb["Bills-2026"]
        assert ws["B28"].value == "=SUM(B3:B26)"   # inflating range
        assert ws["B29"].value == "=SUM(B3:B14)"   # true range
        # intermediate subtotal rows exist inside the inflated range
        assert ws["B15"].value == "=B3"

    def test_b_ref_broken_balance(self, wb: Workbook) -> None:
        ws = wb["Family-Loan"]
        refs = [ws.cell(row=r, column=4).value for r in range(2, 14)]
        assert sum(1 for v in refs if v == "=#REF!+0") == 7
        assert all(isinstance(v, int) for v in refs[:5])

    def test_c_annual_ne_monthly_sum(self, wb: Workbook) -> None:
        ws = wb["Budget-2024"]
        literal = ws["B14"].value
        true_sum = sum(180000 + i * 1000 for i in range(12))
        assert literal == 2400000 and literal != true_sum

    def test_d_hardcoded_paye_ni(self, wb: Workbook) -> None:
        ws = wb["Payroll"]
        assert ws["C2"].value == "=B2*0.2"
        assert ws["D2"].value == "=B2*0.08"
        # no reference to Tax-Tables anywhere in the formulas
        assert all("Tax-Tables" not in str(ws.cell(row=r, column=c).value)
                   for r in range(2, 14) for c in (3, 4))

    def test_e_forecast_overwritten(self, wb: Workbook) -> None:
        ws = wb["Budget-2027"]
        # months 4-6 (rows 5-7) are literal actuals in the forecast column
        assert all(isinstance(ws.cell(row=r, column=2).value, int)
                   for r in (5, 6, 7))
        assert all(ws.cell(row=r, column=2).value == "=200000"
                   for r in (2, 3, 4, 8, 9, 10, 11, 12, 13))

    def test_f_out_of_range_period(self, wb: Workbook) -> None:
        ws = wb["Payroll"]
        assert "Payroll-Old'!C14" in str(ws["B15"].value)
        old = wb["Payroll-Old"]
        assert old["A15"].value == "P14"  # the out-of-range anchor exists


class TestFictionality:
    """Fixture policy: no real-looking values — spot-check the entities."""

    def test_fictional_entities(self, wb: Workbook) -> None:
        assert wb["Income"]["B2"].value == "ACME Manufacturing Ltd"
        # every salary gross is a round fictional number
        assert all(ws.value % 100 == 0
                   for ws in wb["Income"]["C2":"C13"][0]
                   for ws in [ws])
