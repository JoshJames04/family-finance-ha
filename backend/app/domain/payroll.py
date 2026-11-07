"""Employment + effective-dated salary/tax-code revisions (issue #78).

Revision series never overlap: the unique (employment_id,
effective_date) key plus a domain guard rejects same-date duplicates —
a revision supersedes its predecessor from its effective date.

Proration: a mid-period salary change splits the period at each
revision boundary and sums ``annual_pence * days / 365`` per segment
(daily-rate convention, deterministic integer rounding).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    Employment,
    SalaryRevision,
    TaxCodeRevision,
)

WEEKS_PER_YEAR = Decimal(52)
DAYS_PER_YEAR = 365


class PayrollError(ValueError):
    pass


def create_employment(session: Session, employment: Employment) -> None:
    session.add(employment)
    session.flush()


def add_salary_revision(
    session: Session, employment: Employment, effective_date: date,
    annual_pence: int, notes: str | None = None,
) -> SalaryRevision:
    if annual_pence < 0:
        raise PayrollError("annual salary cannot be negative")
    if _revision_on(session, SalaryRevision, employment.id,
                    effective_date) is not None:
        raise PayrollError(
            f"salary revision already exists on {effective_date}")
    rev = SalaryRevision(employment_id=employment.id,
                         effective_date=effective_date,
                         annual_salary_pence=annual_pence, notes=notes)
    session.add(rev)
    session.flush()
    return rev


def add_tax_code_revision(
    session: Session, employment: Employment, effective_date: date,
    tax_code: str, notes: str | None = None,
) -> TaxCodeRevision:
    if _revision_on(session, TaxCodeRevision, employment.id,
                    effective_date) is not None:
        raise PayrollError(
            f"tax code revision already exists on {effective_date}")
    rev = TaxCodeRevision(employment_id=employment.id,
                          effective_date=effective_date,
                          tax_code=tax_code, notes=notes)
    session.add(rev)
    session.flush()
    return rev


def _revision_on[RevT: (SalaryRevision, TaxCodeRevision)](
    session: Session, model: type[RevT], employment_id: int, on: date,
) -> RevT | None:
    return session.execute(
        select(model).where(
            model.employment_id == employment_id,
            model.effective_date == on)
    ).scalar_one_or_none()


def _effective[RevT: (SalaryRevision, TaxCodeRevision)](
    session: Session, model: type[RevT], employment_id: int, on: date,
) -> RevT | None:
    """Latest revision with effective_date <= `on`, else None."""
    return session.execute(
        select(model).where(
            model.employment_id == employment_id,
            model.effective_date <= on)
        .order_by(model.effective_date.desc()).limit(1)
    ).scalar_one_or_none()


def salary_at(session: Session, employment_id: int, on: date) -> int:
    """Annual salary (pence) in force on `on`."""
    rev = _effective(session, SalaryRevision, employment_id, on)
    if rev is None:
        raise PayrollError(
            f"no salary revision effective on or before {on}")
    return int(rev.annual_salary_pence)


def tax_code_at(session: Session, employment_id: int, on: date) -> str:
    rev = _effective(session, TaxCodeRevision, employment_id, on)
    if rev is None:
        raise PayrollError(
            f"no tax code revision effective on or before {on}")
    return str(rev.tax_code)


def hourly_rate_pence(annual_pence: int,
                      contracted_hours: Decimal) -> int:
    """annual / (contracted_hours * 52), rounded to the penny."""
    if contracted_hours <= 0:
        raise PayrollError("contracted hours must be positive")
    per_week = Decimal(annual_pence) / WEEKS_PER_YEAR
    return int((per_week / contracted_hours).quantize(
        Decimal("1")))


def gross_for_period(
    session: Session, employment_id: int, start: date, end: date,
) -> int:
    """Gross pay for [start, end] inclusive (pence).

    The period is split at each salary-revision boundary; each segment
    contributes ``annual * days // 365``. A mid-period rise therefore
    prorates deterministically.
    """
    if end < start:
        raise PayrollError("period end precedes start")
    revs = list(session.execute(
        select(SalaryRevision).where(
            SalaryRevision.employment_id == employment_id,
            SalaryRevision.effective_date <= end)
        .order_by(SalaryRevision.effective_date)
    ).scalars().all())
    if not revs:
        raise PayrollError("no salary revisions in or before period")

    # boundary points where the rate changes inside (start, end]
    cuts = [r.effective_date for r in revs
            if start < r.effective_date <= end]
    segments = [start, *cuts, end + timedelta(days=1)]
    total = 0
    pairs = zip(segments, segments[1:], strict=False)
    for seg_start, seg_end in pairs:
        if seg_start >= seg_end:
            continue
        rev = _effective(session, SalaryRevision, employment_id,
                         seg_start)
        if rev is None:
            continue  # before first-ever revision: not yet employed
        days = (seg_end - seg_start).days
        total += int(rev.annual_salary_pence) * days // DAYS_PER_YEAR
    return total
