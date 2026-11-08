"""Bounded local payslip text extraction and structured parsing.

Extraction is advisory only: parsed fields land in
``Payslip.extracted_json`` for review, components stay editable and
confirmation is always an explicit reconcile action. Parsing is
pure-stdlib regex work over the extracted text — no extra
dependencies — and every failure path degrades to manual entry with a
human-readable warning. ``extract_text`` never raises.
"""

from __future__ import annotations

import re
import subprocess
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO
from typing import NamedTuple

from app.persistence.models import PayrollComponentKind

_MAX_PDF_PAGES = 20
_MAX_PAGE_CHARS = 50_000
_MAX_TEXT_CHARS = 200_000
_OCR_TIMEOUT_SECONDS = 10

# Kinds that add to gross; every other kind is a deduction and stores a
# negative actual, matching the ``payrun``/``payroll_totals`` convention.
_EARNING_KINDS = frozenset({
    PayrollComponentKind.base_salary,
    PayrollComponentKind.overtime,
    PayrollComponentKind.bonus,
    PayrollComponentKind.allowance,
})
_DEDUCTION_KINDS = frozenset(
    kind for kind in PayrollComponentKind if kind not in _EARNING_KINDS)

_DISPLAY: dict[PayrollComponentKind, str] = {
    PayrollComponentKind.base_salary: "basic/base salary",
    PayrollComponentKind.overtime: "overtime pay",
    PayrollComponentKind.bonus: "bonus",
    PayrollComponentKind.allowance: "allowance",
    PayrollComponentKind.unpaid_leave: "unpaid leave",
    PayrollComponentKind.pension: "pension",
    PayrollComponentKind.tax: "income tax",
    PayrollComponentKind.ni: "National Insurance",
    PayrollComponentKind.student_loan: "student loan",
}

# Overtime-ish line labels. Extend this tuple when employers use new
# wording — only lines carrying one of these labels may produce an
# ``overtime_lines`` entry, so a generic ``x @ y`` row is never assumed
# to be overtime.
OVERTIME_LABELS: tuple[str, ...] = (
    r"overtime",
    r"o\s*/\s*t",
    r"\bo\.?\s*t\.?\b",
    r"premium",
)

# Ordered so specific phrases are tried before the short generic words
# they contain ("salary sacrifice" before "salary", "income tax" before
# "tax"); overlap resolution also prefers the longest match.
_COMPONENT_LABELS: tuple[tuple[PayrollComponentKind, tuple[str, ...]], ...] = (
    (PayrollComponentKind.unpaid_leave, (r"unpaid\b", r"absence\b")),
    (PayrollComponentKind.student_loan, (
        r"student\s*(?:/|\bor\b|\band\b)?\s*postgraduate\s*loan\b",
        r"student\s+loan\b", r"postgraduate\s+loan\b", r"postgrad\b",
        r"\bs\.?\s*l\.?\s*c\.?\b", r"\bpgl\b", r"\bstudent\b")),
    (PayrollComponentKind.pension, (
        r"salary\s+sacrifice\b", r"pension\b", r"superannuation\b")),
    (PayrollComponentKind.ni, (
        r"national\s+insurance\b", r"\bn\.?\s*i\.?\s*c\.?\b",
        r"\bn\.?\s*i\.?\b")),
    (PayrollComponentKind.tax, (
        r"income\s+tax\b", r"\bp\.?\s*a\.?\s*y\.?\s*e\b", r"\btax\b")),
    (PayrollComponentKind.overtime, OVERTIME_LABELS),
    (PayrollComponentKind.bonus, (
        r"bonus\b", r"commission\b", r"incentive\b")),
    (PayrollComponentKind.allowance, (
        r"allowance\b", r"expenses?\b", r"subsistence\b", r"mileage\b")),
    (PayrollComponentKind.base_salary, (
        r"basic\s+(?:pay|salary)\b", r"base\s+(?:pay|salary)\b",
        r"standard\s+pay\b", r"normal\s+pay\b", r"\bbasic\b",
        r"\bsalary\b")),
)

# ``gross``/``net`` are summary fields, not components, but their labels
# take part in the same segmentation so merged columns split correctly.
_FIELD_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gross", (
        r"total\s+gross\b",
        r"gross\s+(?:pay|salary|earnings|total|income|amount|"
        r"remuneration)\b",
        r"total\s+(?:pay|payments|earnings|remuneration)\b",
        r"\bgross\b(?!\s*deductions?\b)")),
    ("net", (
        r"net\s+(?:pay|payment|salary|payable|amount|wage|earnings)\b",
        r"take[\s-]?home\b",
        r"\bnet\b(?!\s*(?:deductions?|tax|ni)\b)")),
)


def _build_label_specs() -> tuple[tuple[str, re.Pattern[str]], ...]:
    specs: list[tuple[str, re.Pattern[str]]] = []
    for kind, patterns in _COMPONENT_LABELS:
        specs.append((kind.value, re.compile(
            "|".join(f"(?:{pattern})" for pattern in patterns),
            re.IGNORECASE)))
    for field, patterns in _FIELD_LABELS:
        specs.append((field, re.compile(
            "|".join(f"(?:{pattern})" for pattern in patterns),
            re.IGNORECASE)))
    return tuple(specs)


_LABEL_SPECS = _build_label_specs()

# Lines that carry codes, basis figures or employer-side values rather
# than a readable employee component amount.
_ADMIN_RE = re.compile(
    r"\btaxable\b|\bpensionable\b|\bniable\b|\bnino\b|\bni\s*no\.?\b"
    r"|\b(?:tax|ni|n\.i\.?|national\s+insurance)\s*"
    r"(?:code|period|number|table|category|letter|reference|rate|"
    r"week|month|year)\b"
    r"|\bemployer'?s?\b"
    r"|\bstudent\s+(?:number|no\.?|id)\b"
    r"|\b(?:basic|hourly|standard|normal)\s+rate\b|\brate\s+of\s+pay\b",
    re.IGNORECASE)

# Segment-local "to date" aggregates — truncate the segment so a YTD
# total is never mistaken for this period's component.
_YTD_RE = re.compile(
    r"\by\.?\s*t\.?\s*d\b|\bto\s+date\b|\bcumulative\b"
    r"|\byear\s+to\s+date\b|\btotal\s+to\s+date\b|\bthis\s+year\b",
    re.IGNORECASE)

_PAY_DATE_LABEL = re.compile(
    r"\b(?:pay|payment|paid)\s*date\b|\bdate\s+paid\b|\bpaid\s+on\b"
    r"|\bpay\s*day\b|\bdate\s+of\s+payment\b", re.IGNORECASE)
_PERIOD_END_LABEL = re.compile(
    r"\b(?:pay\s*)?period\s*(?:end|ending|ends|to|up\s+to)\b"
    r"|\b(?:week|month|fortnight)\s*(?:end|ending|ends)\b"
    r"|\bpay\s*period\b|\bperiod\s+covered\b|\bperiod\s+of\s+pay\b",
    re.IGNORECASE)

_MONEY_RE = re.compile(
    r"(?<![\w.,£])-?\s*£?\s*\d+(?:[ ,]\d{3})*(?:\.\d{1,2})?(?![\w.,])")

# ``10.0 hrs @ £29.50`` / ``10 HRS AT 29.50`` — the hours unit is
# optional only for the explicit ``@``/``x`` separators.
_OT_LINE_RE = re.compile(
    r"(?<![\d.,])(?P<hours>\d{1,3}(?:\.\d{1,2})?)\s*"
    r"(?:h(?:rs?|ours?)?\.?\s*)?(?:@|\bat\b|\bx\b)\s*"
    r"(?P<rate>-?£?\s*\d[\d ,]*(?:\.\d{1,2})?)",
    re.IGNORECASE)

# An OCR ``0`` sitting inside a word (``0VERTIME``/``T0TAL``) is a
# letter ``O``; digits are left untouched.
_ZERO_TO_O = re.compile(
    r"(?<![\w.,])0(?=[A-Za-z])|(?<=[A-Za-z])0(?=[A-Za-z])")

_PERCENT_RE = re.compile(r"\d+(?:[.,]\d+)?\s*%")

_MONTH_NUMBER = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3,
    "march": 3, "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12}
_MONTH_RE = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?")

_DATE_RES: tuple[re.Pattern[str], ...] = (
    # 28 April 2026 / 28th Apr 26
    re.compile(
        rf"(?<![\w.])(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+"
        rf"(?P<m>{_MONTH_RE})\.?,?\s+(?P<y>\d{{2,4}})\b",
        re.IGNORECASE),
    # April 28, 2026 — four-digit year so "April 2026" alone is not
    # mistaken for day 20, year 26.
    re.compile(
        rf"(?<![\w.])(?P<m>{_MONTH_RE})\.?\s+(?P<d>\d{{1,2}})"
        rf"(?:st|nd|rd|th)?\s*,?\s*(?P<y>\d{{4}})\b", re.IGNORECASE),
    # 28/04/2026 or 28-04-26 (UK day-first)
    re.compile(
        r"(?<![\d/.-])(?P<d>\d{1,2})\s*[/.-]\s*(?P<m>\d{1,2})\s*[/.-]\s*"
        r"(?P<y>\d{2,4})(?![\d/.-])"),
    # 2026-04-28
    re.compile(
        r"(?<![\d/.-])(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"
        r"(?![\d/.-])"),
)


class _Hit(NamedTuple):
    """A label match inside a line; ``label`` is a component kind value
    or the ``gross``/``net`` summary field."""

    start: int
    end: int
    label: str


def _blank() -> dict[str, object]:
    """The empty structured payload returned on every failure path so
    the stored shape stays consistent for manual entry."""
    return {
        "components": {}, "overtime_lines": [], "gross_pence": None,
        "net_pence": None, "pay_date": None, "period_end": None,
        "text": "", "confidence": "low"}


def _gbp(pence: int) -> str:
    sign = "-" if pence < 0 else ""
    pounds = Decimal(abs(pence)) / Decimal(100)
    return f"{sign}£{pounds:,.2f}"


def _money_pence(raw: str) -> int | None:
    """``£1,234.56``/``1234.56``/``-£1,234`` → integer pence via
    ``Decimal`` — never float."""
    cleaned = raw.replace("£", "").replace(",", "").replace(" ", "")
    cleaned = cleaned.strip()
    negative = cleaned.startswith("-")
    cleaned = cleaned.lstrip("+-")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        return None
    try:
        pence = int((Decimal(cleaned) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None
    return -pence if negative else pence


def _money_spans(segment: str) -> list[tuple[int, int, int]]:
    """``(start, end, pence)`` for each money token in *segment*;
    parenthesised values are treated as negative."""
    found: list[tuple[int, int, int]] = []
    for match in _MONEY_RE.finditer(segment):
        pence = _money_pence(match.group())
        if pence is None:
            continue
        if (match.start() > 0 and segment[match.start() - 1] == "("
                and match.end() < len(segment)
                and segment[match.end()] == ")"):
            pence = -abs(pence)
        found.append((match.start(), match.end(), pence))
    return found


def _iso_date(day: str, month: str, year: str) -> str | None:
    try:
        month_no = (int(month) if month.isdigit()
                    else _MONTH_NUMBER[month.lower().rstrip(".")])
        year_no = int(year)
        if year_no < 100:
            year_no += 2000
        return date(year_no, month_no, int(day)).isoformat()
    except (KeyError, ValueError):
        return None


def _dates_on(line: str) -> list[tuple[int, int, str]]:
    """``(start, end, iso)`` for every parseable date, left to right."""
    found: list[tuple[int, int, str]] = []
    for pattern in _DATE_RES:
        for match in pattern.finditer(line):
            iso = _iso_date(
                match.group("d"), match.group("m"), match.group("y"))
            if iso is not None:
                found.append((match.start(), match.end(), iso))
    out: list[tuple[int, int, str]] = []
    end = -1
    for item in sorted(found, key=lambda t: (t[0], t[0] - t[1])):
        if item[0] < end:
            continue
        out.append(item)
        end = item[1]
    return out


def _mask_line(line: str, scanned: str,
               dates: list[tuple[int, int, str]]) -> str:
    """Blank date and percentage tokens (same length) so they are never
    read as money amounts."""
    spans = [(start, end) for start, end, _ in dates]
    spans.extend(match.span() for match in _PERCENT_RE.finditer(scanned))
    chars = list(line)
    for start, end in spans:
        for index in range(start, min(end, len(chars))):
            chars[index] = " "
    return "".join(chars)


def _label_hits(line: str) -> list[_Hit]:
    """Sorted, non-overlapping label matches; earliest start then
    longest match wins."""
    matches: list[_Hit] = []
    for label, pattern in _LABEL_SPECS:
        matches.extend(
            _Hit(match.start(), match.end(), label)
            for match in pattern.finditer(line))
    hits: list[_Hit] = []
    end = -1
    for match in sorted(matches, key=lambda h: (h.start, h.start - h.end)):
        if match.start < end:
            continue
        hits.append(match)
        end = match.end
    return hits


def _ocr_text(data: bytes) -> tuple[str | None, list[str]]:
    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--dpi", "150"],
            input=data, capture_output=True,
            timeout=_OCR_TIMEOUT_SECONDS, check=False)
        text = result.stdout.decode("utf-8", errors="replace")[:100_000]
        if text.strip():
            return text, [
                "OCR is advisory; verify every component against the payslip."]
        return None, ["OCR found no text; enter components manually."]
    except (OSError, subprocess.TimeoutExpired):
        return None, [
            "Local OCR is unavailable or timed out; enter components manually."]


def _parse(text: str) -> tuple[dict[str, object], list[str]]:
    """Parse extracted payslip text into structured fields.

    Only explicitly labelled lines produce values — base_salary and
    friends are never inferred by subtracting other figures.
    """
    warnings: list[str] = []
    components: dict[str, int] = {}
    overtime_lines: list[dict[str, object]] = []
    warned: set[str] = set()
    gross_pence: int | None = None
    net_pence: int | None = None
    pay_date: str | None = None
    period_end: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not any(char.isdigit() for char in line):
            continue
        scanned = _ZERO_TO_O.sub("o", line)
        dates = _dates_on(scanned)
        if (pay_date is None and dates
                and _PAY_DATE_LABEL.search(scanned)):
            pay_date = dates[0][2]
        if (period_end is None and dates
                and _PERIOD_END_LABEL.search(scanned)):
            period_end = dates[-1][2]
        if _ADMIN_RE.search(scanned):
            continue
        hits = _label_hits(scanned)
        if not hits:
            continue
        masked = _mask_line(line, scanned, dates)
        for index, hit in enumerate(hits):
            seg_end = (hits[index + 1].start if index + 1 < len(hits)
                       else len(masked))
            segment = masked[hit.end:seg_end]
            ytd = _YTD_RE.search(segment)
            if ytd is not None:
                segment = segment[:ytd.start()]
            tokens = _money_spans(segment)
            if hit.label in {"gross", "net"}:
                # Summary fields: label is followed by its amount.
                if tokens:
                    if hit.label == "gross":
                        gross_pence = tokens[0][2]
                    else:
                        net_pence = tokens[0][2]
                elif hit.label not in warned:
                    warned.add(hit.label)
                    warnings.append(
                        f"{hit.label} pay could not be read; "
                        "enter it manually.")
                continue
            kind = PayrollComponentKind(hit.label)
            amount: int | None = None
            ot = (_OT_LINE_RE.search(segment)
                  if kind in _EARNING_KINDS else None)
            if ot is not None:
                rate_pence = _money_pence(ot.group("rate"))
                others = [t for t in tokens
                          if t[1] <= ot.start() or t[0] >= ot.end()]
                if others:
                    amount = others[-1][2]
                elif rate_pence is not None:
                    # ``10 hrs @ £29.50`` prints no total — the product
                    # is the line's labelled value, not an inference.
                    amount = int((Decimal(ot.group("hours")) * rate_pence)
                                 .quantize(Decimal("1"),
                                           rounding=ROUND_HALF_UP))
                if (kind is PayrollComponentKind.overtime
                        and rate_pence is not None):
                    overtime_lines.append({
                        "id": f"line-{len(overtime_lines) + 1}",
                        "hours": str(Decimal(ot.group("hours"))),
                        "printed_rate_pence": rate_pence})
            elif tokens:
                amount = tokens[-1][2]
            if amount is None:
                if kind.value not in warned:
                    warned.add(kind.value)
                    warnings.append(
                        f"{_DISPLAY[kind]} could not be read; "
                        "enter it manually.")
                continue
            components[kind.value] = components.get(kind.value, 0) + (
                amount if kind in _EARNING_KINDS else -abs(amount))

    deduction_found = any(
        kind.value in components for kind in _DEDUCTION_KINDS)
    if not components and gross_pence is None and net_pence is None:
        warnings.append(
            "no labelled payroll components detected; "
            "enter values manually.")
    elif gross_pence is None or net_pence is None or not deduction_found:
        warnings.append(
            "only partial components detected; "
            "enter missing values manually.")
    if gross_pence is not None and components:
        earnings = sum(
            components.get(kind.value, 0) for kind in _EARNING_KINDS)
        remainder = gross_pence - earnings
        if remainder != 0:
            warnings.append(
                f"labelled earnings total {_gbp(earnings)} but gross is "
                f"{_gbp(gross_pence)}; the {_gbp(remainder)} remainder "
                "is not attributed to a labelled line and is never "
                "inferred — enter it manually.")
    if gross_pence is not None and net_pence is not None:
        deductions = sum(
            components.get(kind.value, 0) for kind in _DEDUCTION_KINDS)
        if gross_pence + deductions != net_pence:
            warnings.append(
                "labelled figures do not reconcile: gross minus "
                f"deductions is {_gbp(gross_pence + deductions)} but "
                f"net is {_gbp(net_pence)}; verify every component "
                "manually.")
    confidence = (
        "high" if gross_pence is not None and net_pence is not None
        and deduction_found else "low")
    result: dict[str, object] = {
        "components": components,
        "overtime_lines": overtime_lines,
        "gross_pence": gross_pence,
        "net_pence": net_pence,
        "pay_date": pay_date,
        "period_end": period_end,
        "text": text,
        "confidence": confidence}
    return result, warnings


def extract_text(
    data: bytes, media_type: str
) -> tuple[dict[str, object], list[str]]:
    """Extract payslip text plus best-effort structured fields.

    Returns ``(extracted, warnings)``. Every failure path returns the
    empty payload shape plus a warning so manual entry can continue —
    this function never raises.
    """
    if media_type in {"image/png", "image/jpeg"}:
        text, warnings = _ocr_text(data)
        if text is None:
            return _blank(), warnings
        parsed, parse_warnings = _parse(text)
        return parsed, warnings + parse_warnings
    if media_type != "application/pdf":
        return _blank(), [
            "Unsupported payslip media; enter components manually."]
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            return _blank(), [
                "Payslip PDF is encrypted; enter components manually."]
        if len(reader.pages) > _MAX_PDF_PAGES:
            return _blank(), [
                f"PDF has more than {_MAX_PDF_PAGES} pages; "
                "enter components manually."]
        text = "\n".join(
            (page.extract_text() or "")[:_MAX_PAGE_CHARS]
            for page in reader.pages)
        if not text.strip():
            return _blank(), [
                "No text layer found; enter components manually."]
        parsed, warnings = _parse(text[:_MAX_TEXT_CHARS])
        return parsed, [
            "Text extraction is advisory; verify every component "
            "against the payslip.", *warnings]
    except Exception:
        return _blank(), [
            "Payslip text extraction failed; enter components manually."]
