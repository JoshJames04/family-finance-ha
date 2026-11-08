"""UK statutory deduction engine (issue #81).

Pure functions driven entirely by versioned ``tax_rule_set`` params —
zero statutory constants in logic. Money in integer pence; rates in
basis points; all deductions floor to the penny per HMRC convention.

PAYE is cumulative: period tax = cumulative tax due on cumulative
income minus tax already paid, with allowances/bands pro-rated by the
tax month. W1/M1 codes reset to a period-only basis. NI and student
loans are always period-only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    PensionMode,
    TaxJurisdiction,
    TaxRuleSet,
)

__all__ = ["PensionMode", "TaxCode", "Deductions", "TaxError",
           "compute_period", "parse_tax_code", "rule_set",
           "seed_rule_sets", "embedded_rule_sets", "tax_year_for",
           "tax_month_for"]

_DATA = Path(__file__).resolve().parent / "data" / "uk_tax_rules.json"

MONTHS_PER_YEAR = 12
FLAT_CODES = {"BR", "D0", "D1", "0T", "NT"}
_CODE_NUM = re.compile(r"^(S|C|W)?(\d+)(L|T|M|N)$")
_CODE_K = re.compile(r"^(S|C|W)?K(\d+)$")


class TaxError(ValueError):
    pass


@dataclass(frozen=True)
class TaxCode:
    allowance_pence: int          # may be negative (K codes)
    cumulative: bool              # False → W1/M1 period-only basis
    flat_rate_bps: int | None     # BR=2000 D0=4000 D1=4500; NT → no tax
    prefix: str                   # '', 'S', 'C' → jurisdiction override


@dataclass(frozen=True)
class Deductions:
    taxable_gross_pence: int
    tax_pence: int
    ni_pence: int
    student_loan_pence: int
    pension_pence: int
    # Contribution deducted from NET pay — nonzero only for relief at
    # source; net_pay/sacrifice already sit inside taxable_gross.
    net_pension_deduction_pence: int = 0

    @property
    def net_pence(self) -> int:
        return (self.taxable_gross_pence - self.tax_pence
                - self.ni_pence - self.student_loan_pence
                - self.net_pension_deduction_pence)


def parse_tax_code(raw: str) -> TaxCode:
    """1257L / S1257L / K500 / BR / D0 / D1 / 0T / NT (+ W1M1 suffix)."""
    code = raw.strip().upper().replace(" ", "")
    cumulative = True
    for marker in (" X", "W1", "M1"):
        if code.endswith(marker.strip()):
            cumulative = False
            code = code[: -len(marker.strip())] or code
            break
    flat = {"BR": 2000, "D0": 4000, "D1": 4500, "NT": 0}
    if code in flat:
        return TaxCode(allowance_pence=0, cumulative=False,
                       flat_rate_bps=flat[code], prefix="")
    if code == "0T":
        return TaxCode(allowance_pence=0, cumulative=cumulative,
                       flat_rate_bps=None, prefix="")
    if m := _CODE_K.match(code):
        prefix, num = m.group(1) or "", int(m.group(2))
        return TaxCode(allowance_pence=-num * 1000,
                       cumulative=cumulative, flat_rate_bps=None,
                       prefix=prefix)
    if m := _CODE_NUM.match(code):
        prefix, num = m.group(1) or "", int(m.group(2))
        return TaxCode(allowance_pence=num * 1000,
                       cumulative=cumulative, flat_rate_bps=None,
                       prefix=prefix)
    raise TaxError(f"unrecognised tax code: {raw!r}")


def _banded(taxable: int, bands: list[dict[str, Any]],
            months: int) -> int:
    """Cumulative banded tax on `taxable` pence with annual band limits
    pro-rated by `months`/12."""
    tax, lower = 0, 0
    for band in bands:
        limit = band["limit"]
        top = (limit * months // MONTHS_PER_YEAR
               if limit is not None else None)
        upper = taxable if top is None else min(taxable, top)
        if upper > lower:
            tax += (upper - lower) * int(band["rate"]) // 10_000
        if top is None or taxable <= top:
            break
        lower = top
    return tax


def _load_params(row: TaxRuleSet) -> dict[str, Any]:
    return json.loads(row.params)  # type: ignore[no-any-return]


def seed_rule_sets(session: Session) -> int:
    """Insert embedded rule sets missing from tax_rule_set. Existing
    rows are never touched — corrections arrive as a new version."""
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    added = 0
    for s in raw["sets"]:
        exists = session.execute(
            select(TaxRuleSet).where(
                TaxRuleSet.tax_year == s["tax_year"],
                TaxRuleSet.jurisdiction == s["jurisdiction"],
                TaxRuleSet.version == s["version"])
        ).scalar_one_or_none()
        if exists is None:
            session.add(TaxRuleSet(
                tax_year=s["tax_year"],
                jurisdiction=TaxJurisdiction(s["jurisdiction"]),
                version=int(s["version"]),
                params=json.dumps(s["params"], separators=(",", ":"))))
            added += 1
    session.flush()
    return added


def rule_set(session: Session, tax_year: str,
             jurisdiction: TaxJurisdiction) -> TaxRuleSet:
    row = session.execute(
        select(TaxRuleSet).where(
            TaxRuleSet.tax_year == tax_year,
            TaxRuleSet.jurisdiction == jurisdiction)
        .order_by(TaxRuleSet.version.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        seed_rule_sets(session)
        row = session.execute(
            select(TaxRuleSet).where(
                TaxRuleSet.tax_year == tax_year,
                TaxRuleSet.jurisdiction == jurisdiction)
            .order_by(TaxRuleSet.version.desc()).limit(1)
        ).scalar_one_or_none()
    if row is None:
        raise TaxError(f"no rule set for {tax_year}/{jurisdiction}")
    return row


def latest_rule_set(session: Session,
                    jurisdiction: TaxJurisdiction) -> TaxRuleSet | None:
    """Newest rule set for a jurisdiction regardless of year — the
    fallback for forecasting beyond published HMRC tables."""
    return session.execute(
        select(TaxRuleSet).where(
            TaxRuleSet.jurisdiction == jurisdiction)
        .order_by(TaxRuleSet.tax_year.desc(),
                  TaxRuleSet.version.desc()).limit(1)
    ).scalar_one_or_none()


def _allowance(code: TaxCode, params: dict[str, Any],
               cum_income: int, months: int) -> int:
    """Effective cumulative allowance incl. PA taper."""
    base = code.allowance_pence * months // MONTHS_PER_YEAR
    taper_start = int(params["taper_start"]) * months // MONTHS_PER_YEAR
    over = max(0, cum_income - taper_start)
    tapered = base - over // int(params["taper_divisor"])
    # K codes keep their negative allowance; positive codes floor at 0.
    return max(0, tapered) if code.allowance_pence >= 0 else base


def compute_period(
    *,
    gross_pence: int,
    tax_code: str,
    params: dict[str, Any],
    period: int = 1,                     # tax month 1..12
    ytd_taxable_gross_pence: int = 0,
    ytd_tax_pence: int = 0,
    ni_category: str = "A",
    student_loan_plan: str = "none",
    pension_mode: PensionMode = PensionMode.none,
    pension_pence: int = 0,
) -> Deductions:
    """One payroll period's deductions for a monthly-paid employee."""
    if not 1 <= period <= 12:
        raise TaxError("period must be a tax month 1..12")
    code = parse_tax_code(tax_code)

    # Pension treatment determines which base each deduction sees.
    if pension_mode is PensionMode.salary_sacrifice:
        taxable_gross = ni_gross = gross_pence - pension_pence
    elif pension_mode is PensionMode.net_pay:
        taxable_gross = gross_pence - pension_pence
        ni_gross = gross_pence
    else:  # none / relief_at_source — RAS is claimed back, not deducted
        taxable_gross = ni_gross = gross_pence

    months = period if code.cumulative else 1
    cum_income = (ytd_taxable_gross_pence + taxable_gross
                  if code.cumulative else taxable_gross)
    prior_tax = ytd_tax_pence if code.cumulative else 0

    if code.flat_rate_bps is not None:
        tax = taxable_gross * code.flat_rate_bps // 10_000
    else:
        allow = _allowance(code, params, cum_income, months)
        taxable = max(0, cum_income - allow)
        tax = max(0, _banded(taxable, params["bands"], months)
                  - prior_tax)

    # NI: monthly thresholds, non-cumulative.
    ni = params["ni"]
    pt = int(ni["primary_threshold"]) // MONTHS_PER_YEAR
    uel = int(ni["upper_earnings_limit"]) // MONTHS_PER_YEAR
    below_r, above_r = ni["categories"].get(
        ni_category.upper(), ni["categories"]["A"])
    ni_pence = (
        max(0, min(ni_gross, uel) - pt) * int(below_r) // 10_000
        + max(0, ni_gross - uel) * int(above_r) // 10_000)

    # Student loan: monthly threshold, period-only, floor to penny.
    sl = params["student_loans"].get(student_loan_plan)
    sl_pence = 0
    if sl is not None:
        threshold = int(sl[0]) // MONTHS_PER_YEAR
        sl_pence = max(0, taxable_gross - threshold) * int(sl[1]) // 10_000

    return Deductions(
        taxable_gross_pence=taxable_gross, tax_pence=tax,
        ni_pence=ni_pence, student_loan_pence=sl_pence,
        pension_pence=pension_pence,
        net_pension_deduction_pence=(
            pension_pence
            if pension_mode is PensionMode.relief_at_source else 0))


@lru_cache(maxsize=1)
def embedded_rule_sets() -> tuple[dict[str, Any], ...]:
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    return tuple(raw["sets"])


def tax_year_for(d: date) -> str:
    """UK tax year label for a date — 6 Apr 2025…5 Apr 2026 → '2025-26'."""
    start = d.year if (d.month, d.day) >= (4, 6) else d.year - 1
    return f"{start}-{str(start + 1)[2:]}"


def tax_month_for(d: date) -> int:
    """HMRC tax month 1..12 (month 1 = 6 Apr–5 May) for a date."""
    anchor = (d.month - 1 if d.month > 1 else 12) if d.day < 6 \
        else d.month
    return ((anchor - 4) % 12) + 1
