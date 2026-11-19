"""Employment + effective-dated salary/tax-code revisions (issue #78).

Revision series never overlap: the unique (employment_id,
effective_date) key plus a domain guard rejects same-date duplicates —
a revision supersedes its predecessor from its effective date.

Proration: a mid-period salary change splits the period at each
revision boundary and sums ``annual_pence * days / 365`` per segment
(daily-rate convention, deterministic integer rounding).
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from types import EllipsisType

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    Employment,
    PayrollRun,
    PayrollRunStatus,
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


def invalidate_forecasts(session: Session, employment_id: int) -> None:
    """Configuration changes require review, never a silent historical rewrite."""
    for run in session.scalars(
        select(PayrollRun).where(
            PayrollRun.employment_id == employment_id,
            PayrollRun.status == PayrollRunStatus.forecast,
        )
    ):
        run.forecast_stale = True


def add_salary_revision(
    session: Session,
    employment: Employment,
    effective_date: date,
    annual_pence: int,
    notes: str | None = None,
    *,
    shares_owned: Decimal | None = None,
) -> SalaryRevision:
    if shares_owned is None:
        prior = _effective(session, SalaryRevision, employment.id, effective_date)
        shares_owned = prior.shares_owned if prior else Decimal("0")
    if (
        not shares_owned.is_finite()
        or shares_owned < 0
        or shares_owned > Decimal("9999999999.9999")
        or shares_owned != shares_owned.quantize(Decimal("0.0001"))
    ):
        raise PayrollError("shares owned must be non-negative with at most four decimal places")
    if annual_pence < 0:
        raise PayrollError("annual salary cannot be negative")
    if _revision_on(session, SalaryRevision, employment.id, effective_date) is not None:
        raise PayrollError(f"salary revision already exists on {effective_date}")
    rev = SalaryRevision(
        employment_id=employment.id,
        effective_date=effective_date,
        annual_salary_pence=annual_pence,
        notes=notes,
        shares_owned=shares_owned,
    )
    session.add(rev)
    invalidate_forecasts(session, employment.id)
    session.flush()
    return rev


def update_salary_revision(
    session: Session,
    rev: SalaryRevision,
    *,
    effective_date: date | None = None,
    annual_pence: int | None = None,
    notes: str | EllipsisType | None = ...,
    shares_owned: Decimal | None = None,
) -> SalaryRevision:
    """Correct a revision's figure, date or note in place."""
    previous_pay = (rev.effective_date, rev.annual_salary_pence)
    if shares_owned is not None:
        if (
            not shares_owned.is_finite()
            or shares_owned < 0
            or shares_owned > Decimal("9999999999.9999")
            or shares_owned != shares_owned.quantize(Decimal("0.0001"))
        ):
            raise PayrollError("shares owned must be non-negative with at most four decimal places")
        rev.shares_owned = shares_owned
    if annual_pence is not None and annual_pence < 0:
        raise PayrollError("annual salary cannot be negative")
    if effective_date is not None and effective_date != rev.effective_date:
        clash = _revision_on(session, SalaryRevision, rev.employment_id, effective_date)
        if clash is not None and clash.id != rev.id:
            raise PayrollError(f"salary revision already exists on {effective_date}")
        rev.effective_date = effective_date
    if annual_pence is not None:
        rev.annual_salary_pence = annual_pence
    if notes is not ...:
        rev.notes = notes
    if (rev.effective_date, rev.annual_salary_pence) != previous_pay:
        invalidate_forecasts(session, rev.employment_id)
    session.flush()
    return rev


def delete_salary_revision(
    session: Session,
    rev: SalaryRevision,
) -> None:
    """Remove a revision; an employment must keep at least one."""
    remaining = session.execute(
        select(SalaryRevision.id).where(
            SalaryRevision.employment_id == rev.employment_id, SalaryRevision.id != rev.id
        )
    ).first()
    if remaining is None:
        raise PayrollError("cannot delete the only salary revision — delete the employment instead")
    invalidate_forecasts(session, rev.employment_id)
    session.delete(rev)
    session.flush()


def add_tax_code_revision(
    session: Session,
    employment: Employment,
    effective_date: date,
    tax_code: str,
    notes: str | None = None,
) -> TaxCodeRevision:
    if _revision_on(session, TaxCodeRevision, employment.id, effective_date) is not None:
        raise PayrollError(f"tax code revision already exists on {effective_date}")
    rev = TaxCodeRevision(
        employment_id=employment.id, effective_date=effective_date, tax_code=tax_code, notes=notes
    )
    session.add(rev)
    invalidate_forecasts(session, employment.id)
    session.flush()
    return rev


def _revision_on[RevT: (SalaryRevision, TaxCodeRevision)](
    session: Session,
    model: type[RevT],
    employment_id: int,
    on: date,
) -> RevT | None:
    return session.execute(
        select(model).where(model.employment_id == employment_id, model.effective_date == on)
    ).scalar_one_or_none()


def _effective[RevT: (SalaryRevision, TaxCodeRevision)](
    session: Session,
    model: type[RevT],
    employment_id: int,
    on: date,
) -> RevT | None:
    """Latest revision with effective_date <= `on`, else None."""
    return session.execute(
        select(model)
        .where(model.employment_id == employment_id, model.effective_date <= on)
        .order_by(model.effective_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def salary_at(session: Session, employment_id: int, on: date) -> int:
    """Annual salary (pence) in force on `on`."""
    rev = _effective(session, SalaryRevision, employment_id, on)
    if rev is None:
        raise PayrollError(f"no salary revision effective on or before {on}")
    return int(rev.annual_salary_pence)


def tax_code_at(session: Session, employment_id: int, on: date) -> str:
    rev = _effective(session, TaxCodeRevision, employment_id, on)
    if rev is None:
        raise PayrollError(f"no tax code revision effective on or before {on}")
    return str(rev.tax_code)


def hourly_rate_pence(annual_pence: int, contracted_hours: Decimal) -> int:
    """annual / (contracted_hours * 52), rounded to the penny."""
    if contracted_hours <= 0:
        raise PayrollError("contracted hours must be positive")
    per_week = Decimal(annual_pence) / WEEKS_PER_YEAR
    return int((per_week / contracted_hours).quantize(Decimal("1")))


def gross_for_period(
    session: Session,
    employment_id: int,
    start: date,
    end: date,
) -> int:
    """Gross pay for [start, end] inclusive (pence).

    The period is split at each salary-revision boundary; each segment
    contributes ``annual * days // 365``. A mid-period rise therefore
    prorates deterministically.
    """
    if end < start:
        raise PayrollError("period end precedes start")
    revs = list(
        session.execute(
            select(SalaryRevision)
            .where(
                SalaryRevision.employment_id == employment_id, SalaryRevision.effective_date <= end
            )
            .order_by(SalaryRevision.effective_date)
        )
        .scalars()
        .all()
    )
    if not revs:
        raise PayrollError("no salary revisions in or before period")

    # boundary points where the rate changes inside (start, end]
    cuts = [r.effective_date for r in revs if start < r.effective_date <= end]
    segments = [start, *cuts, end + timedelta(days=1)]
    employment = session.get(Employment, employment_id)
    monthly = employment is not None and employment.salary_policy == "equal_monthly"
    total = Fraction(0)
    pairs = zip(segments, segments[1:], strict=False)
    for seg_start, seg_end in pairs:
        if seg_start >= seg_end:
            continue
        rev = _effective(session, SalaryRevision, employment_id, seg_start)
        if rev is None:
            continue  # before first-ever revision: not yet employed
        days = (seg_end - seg_start).days
        if not monthly:
            total += int(rev.annual_salary_pence) * days // DAYS_PER_YEAR
        else:
            cursor = seg_start
            while cursor < seg_end:
                length = calendar.monthrange(cursor.year, cursor.month)[1]
                stop = min(seg_end, cursor.replace(day=length) + timedelta(days=1))
                total += Fraction(int(rev.annual_salary_pence) * (stop - cursor).days, 12 * length)
                cursor = stop
    return int(total + Fraction(1, 2)) if monthly else int(total)
