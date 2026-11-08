"""Structured payslip extraction — synthetic fixtures only.

Every figure below is invented; no real payslip data is used. The PDF
helper emits the smallest valid text-layer PDF pypdf can read.
"""

from __future__ import annotations

import subprocess

import pytest

from app.domain import payslips


def _escape(text: str) -> bytes:
    # PDF literal strings are byte strings — latin-1 keeps ``£`` readable
    # through pypdf's decoding.
    return (
        text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        .encode("latin-1", errors="replace"))


def _pdf(pages: list[list[str]]) -> bytes:
    """Build a minimal valid text PDF — enough for pypdf to read."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids ["
         + " ".join(f"{3 + 2 * index} 0 R" for index in range(len(pages)))
         + f"] /Count {len(pages)} >>").encode(),
    ]
    font_obj = 3 + 2 * len(pages)
    for index, lines in enumerate(pages):
        content_obj = 4 + 2 * index
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_obj} 0 R >> >> "
            f"/Contents {content_obj} 0 R >>".encode())
        stream = (
            b"BT /F1 10 Tf 50 780 Td 14 TL "
            + b" T* ".join(b"(" + _escape(line) + b") Tj" for line in lines)
            + b" ET")
        objects.append(
            b"<< /Length " + str(len(stream)).encode()
            + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF").encode()
    return bytes(out)


def _extract(lines: list[str]):
    return payslips.extract_text(_pdf([lines]), "application/pdf")


TYPICAL_PAYSLIP = [
    "ACME LTD                          PAYSLIP",
    "Tax Code 1257L        NI number AB123456C",
    "Period ending 30 April 2026",
    "Pay date 28 April 2026",
    "Basic Pay                       2,500.00",
    "Overtime 10.0 hrs @ £29.50        295.00",
    "Income Tax                        420.50",
    "National Insurance                168.20",
    "Pension                           125.00",
    "Student Loan                       50.00",
    "Total Deductions                  763.70",
    "Gross Pay                       2,795.00",
    "Net Pay                         2,031.30",
]


def test_typical_uk_payslip_parses_all_fields() -> None:
    extracted, warnings = _extract(TYPICAL_PAYSLIP)
    assert extracted["components"] == {
        "base_salary": 250_000,
        "overtime": 29_500,
        "tax": -42_050,
        "ni": -16_820,
        "pension": -12_500,
        "student_loan": -5_000,
    }
    assert extracted["overtime_lines"] == [
        {"id": "line-1", "hours": "10.0",
         "printed_rate_pence": 2950}]
    assert extracted["gross_pence"] == 279_500
    assert extracted["net_pence"] == 203_130
    assert extracted["pay_date"] == "2026-04-28"
    assert extracted["period_end"] == "2026-04-30"
    assert extracted["confidence"] == "high"
    assert "Basic Pay" in extracted["text"]
    assert warnings == [
        "Text extraction is advisory; verify every component "
        "against the payslip."]


def test_deductions_are_signed_negative() -> None:
    extracted, _ = _extract([
        "Income Tax 420.50",
        "Pension (125.00)",
    ])
    assert extracted["components"]["tax"] == -42_050
    assert extracted["components"]["pension"] == -12_500


def test_currency_variants_parse_to_pence() -> None:
    extracted, _ = _extract([
        "Basic Pay £1,234.56",
        "Bonus 1234.56",
        "Allowance £ 1,250",
    ])
    assert extracted["components"]["base_salary"] == 123_456
    assert extracted["components"]["bonus"] == 123_456
    assert extracted["components"]["allowance"] == 125_000


def test_overtime_label_variants_produce_lines() -> None:
    extracted, _ = _extract([
        "OVERTIME 10.0 HRS AT 29.50 295.00",
        "O/T 4.5 HRS @ £20.00 90.00",
        "PREMIUM 2.0 hrs @ £15.00 30.00",
    ])
    assert extracted["overtime_lines"] == [
        {"id": "line-1", "hours": "10.0", "printed_rate_pence": 2950},
        {"id": "line-2", "hours": "4.5", "printed_rate_pence": 2000},
        {"id": "line-3", "hours": "2.0", "printed_rate_pence": 1500},
    ]
    assert extracted["components"]["overtime"] == 41_500


def test_overtime_line_without_total_uses_labelled_product() -> None:
    extracted, _ = _extract(["OVERTIME 10 hrs @ £29.50"])
    assert extracted["overtime_lines"] == [
        {"id": "line-1", "hours": "10", "printed_rate_pence": 2950}]
    assert extracted["components"]["overtime"] == 29_500


def test_unlabelled_rate_pattern_is_not_overtime() -> None:
    extracted, _ = _extract([
        "10.0 hrs @ £29.50",
        "SHIFT 3 @ 18.25",
    ])
    assert extracted["overtime_lines"] == []
    assert "overtime" not in extracted["components"]


def test_ocr_zero_inside_label_words_is_tolerated() -> None:
    extracted, _ = _extract([
        "0VERTIME 10.0 HRS @ 29.50 295.00",
        "T0TAL GR0SS 2,795.00",
    ])
    assert extracted["overtime_lines"][0]["printed_rate_pence"] == 2950
    assert extracted["gross_pence"] == 279_500


def test_unknown_labels_are_ignored() -> None:
    extracted, warnings = _extract([
        "Wibble Wobble 999.99",
        "Random Thing 1,234.00",
    ])
    assert extracted["components"] == {}
    assert any("no labelled payroll components" in w for w in warnings)


def test_admin_and_basis_lines_are_not_components() -> None:
    extracted, _ = _extract([
        "Tax Code 1257L",
        "Tax Period 4",
        "NI Number AB123456C",
        "Taxable Pay 2,500.00",
        "Employer NI 130.00",
        "Basic Rate 66.50",
        "Income Tax to date 1,682.00",
    ])
    assert extracted["components"] == {}


def test_base_salary_is_never_inferred_from_gross() -> None:
    extracted, warnings = _extract([
        "Gross Pay 2,795.00",
        "Income Tax 420.50",
        "Net Pay 2,031.30",
    ])
    assert "base_salary" not in extracted["components"]
    assert extracted["gross_pence"] == 279_500
    assert any("remainder" in w and "never inferred" in w
               for w in warnings)


def test_partial_extraction_is_low_confidence_with_warning() -> None:
    extracted, warnings = _extract([
        "Basic Pay 2,500.00",
        "Net Pay 2,031.30",
    ])
    assert extracted["confidence"] == "low"
    assert extracted["gross_pence"] is None
    assert any("partial" in w for w in warnings)


def test_unreadable_labelled_line_warns() -> None:
    extracted, warnings = _extract([
        "Income Tax 123X",
        "Net Pay 2,031.30",
    ])
    assert "tax" not in extracted["components"]
    assert any("income tax could not be read" in w for w in warnings)


def test_dates_parse_in_common_uk_formats() -> None:
    extracted, _ = _extract([
        "Payment date: 28/04/2026",
        "Week ending 26 April 2026",
    ])
    assert extracted["pay_date"] == "2026-04-28"
    assert extracted["period_end"] == "2026-04-26"


def test_period_range_uses_last_date_as_period_end() -> None:
    extracted, _ = _extract([
        "Pay period 01/04/2026 - 30/04/2026",
        "Pay date 2026-04-28",
    ])
    assert extracted["period_end"] == "2026-04-30"
    assert extracted["pay_date"] == "2026-04-28"


def test_missing_dates_are_tolerated() -> None:
    extracted, _ = _extract(["Basic Pay 2,500.00"])
    assert extracted["pay_date"] is None
    assert extracted["period_end"] is None


def test_over_twenty_page_pdf_returns_no_components() -> None:
    pages = [[f"filler line {page}"] for page in range(21)]
    extracted, warnings = payslips.extract_text(
        _pdf(pages), "application/pdf")
    assert extracted["components"] == {}
    assert extracted["confidence"] == "low"
    assert any("20 pages" in w for w in warnings)


def test_garbage_pdf_degrades_without_raising() -> None:
    extracted, warnings = payslips.extract_text(
        b"%PDF-totally-broken", "application/pdf")
    assert extracted["components"] == {}
    assert extracted["overtime_lines"] == []
    assert extracted["confidence"] == "low"
    assert warnings


def test_unsupported_media_degrades_without_raising() -> None:
    extracted, warnings = payslips.extract_text(
        b"plain", "text/plain")
    assert extracted["components"] == {}
    assert warnings


def test_pdf_without_text_layer_degrades_without_raising() -> None:
    # A page with no content stream yields no text.
    extracted, warnings = payslips.extract_text(_pdf([[]]), "application/pdf")
    assert extracted["components"] == {}
    assert warnings


class _Completed:
    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout


def test_image_ocr_text_is_parsed_too(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "\n".join([
        "Basic Pay 2,500.00",
        "Income Tax 420.50",
        "Gross Pay 2,500.00",
        "Net Pay 2,079.50",
    ]).encode()
    monkeypatch.setattr(
        payslips.subprocess, "run",
        lambda *a, **k: _Completed(text))
    extracted, warnings = payslips.extract_text(
        b"\x89PNGfake", "image/png")
    assert extracted["components"]["base_salary"] == 250_000
    assert extracted["components"]["tax"] == -42_050
    assert any("OCR is advisory" in w for w in warnings)


def test_ocr_unavailable_degrades_without_raising(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("tesseract", 10)

    monkeypatch.setattr(payslips.subprocess, "run", _raise)
    extracted, warnings = payslips.extract_text(
        b"\x89PNGfake", "image/png")
    assert extracted["components"] == {}
    assert any("unavailable or timed out" in w for w in warnings)


def test_ocr_no_text_degrades_without_raising(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        payslips.subprocess, "run", lambda *a, **k: _Completed(b""))
    extracted, warnings = payslips.extract_text(
        b"\x89PNGfake", "image/png")
    assert extracted["components"] == {}
    assert any("no text" in w for w in warnings)


# Synthetic mirror of a sectioned employer payslip — every figure is
# invented, only the label structure is representative.
SECTIONED_PAYSLIP = [
    "Bob Example",
    "Payslip for Month Ending 30 Sep 2025",
    "EMPLOYEE DETAILS",
    "Works number 123",
    "Department 4 - Widgets (1100)",
    "Tax code S1000L",
    "National Insurance number AA000000A",
    "National Insurance table A",
    "Student Loan plan Plan 1",
    "ADDITIONS",
    "Monthly pay £2,000.00",
    "10 hours @ £10.125 £101.25",
    "3.5 hours @ £15.00 £52.50",
    "2 hours @ £10.125 £20.25",
    "Standby / On Call £20.00",
    "Total £2,194.00",
    "DEDUCTIONS",
    "Tax £250.00",
    "National Insurance £100.00",
    "Student Loan deductions £60.00",
    "Overtime Paid August £30.00",
    "Total £440.00",
    "THIS PAY PERIOD",
    "Taxable gross pay £2,194.00",
    "Employer National Insurance £200.00",
    "Net pay £1,784.00",
    "YEAR TO DATE",
    "Taxable gross pay £9,999.00",
    "Tax £9,999.00",
    "Employee National Insurance £9,999.00",
    "Employer National Insurance £9,999.00",
    "Student Loan deductions £9,999.00",
    "PAYMENT",
    "£1,754.00",
    "Paid on 25/09/2025",
    "Example Widgets Limited PAYE ref 999/X999",
]


def test_sectioned_payslip_layout_parses_all_fields() -> None:
    extracted, warnings = _extract(SECTIONED_PAYSLIP)
    assert extracted["components"] == {
        "base_salary": 200_000,
        # Three addition lines netted with the prior-month clawback.
        "overtime": 10_125 + 5_250 + 2_025 - 3_000,
        "allowance": 2_000,
        "tax": -25_000,
        "ni": -10_000,
        "student_loan": -6_000,
    }
    assert extracted["overtime_lines"] == [
        {"id": "line-1", "hours": "10",
         "printed_rate_pence": 1013},
        {"id": "line-2", "hours": "3.5",
         "printed_rate_pence": 1500},
        {"id": "line-3", "hours": "2",
         "printed_rate_pence": 1013},
    ]
    # Additions total is gross; "Taxable gross pay" agrees.
    assert extracted["gross_pence"] == 219_400
    assert extracted["net_pence"] == 178_400
    # PAYMENT is what reaches the bank and may differ from net pay.
    assert extracted["payment_pence"] == 175_400
    assert extracted["deductions_pence"] == 44_000
    assert extracted["pay_date"] == "2025-09-25"
    assert extracted["period_end"] == "2025-09-30"
    assert extracted["confidence"] == "high"
    assert any(
        "unlabelled addition" in warning for warning in warnings)
    assert any(
        "differs from net pay" in warning for warning in warnings)


def test_sectioned_payslip_year_to_date_never_leaks() -> None:
    extracted, _ = _extract(SECTIONED_PAYSLIP)
    # Every YTD figure is 9,999.00 — if any leaked into components the
    # totals would be far larger than the this-period values asserted
    # above; check the deduction kinds explicitly too.
    assert extracted["components"]["tax"] == -25_000
    assert extracted["components"]["ni"] == -10_000
    assert extracted["components"]["student_loan"] == -6_000
