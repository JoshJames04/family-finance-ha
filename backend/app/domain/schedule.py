"""Expected-movement schedule engine (issue #72).

Unifies recurring commitments, payroll runs and manual plans into a
dated expected-income/expenditure stream — the forecast substrate
Epic 7 reports on. Every row traces to its source via
(source_type, source_id, expected_date); re-materialisation deletes
only FUTURE rows for that source then regenerates, so it is idempotent
and never rewrites history.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.recurring import occurrences
from app.persistence.models import (
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Household,
    PayrollRun,
    PayrollRunStatus,
    RecurringCommitment,
)

DEFAULT_HORIZON_DAYS = 365


def _emit_row(household_id: int, *, account_id: int | None,
              expected_date: date, amount_pence: int, label: str,
              confidence: Confidence, source: ExpectedSource,
              source_id: int) -> ExpectedMovement:
    return ExpectedMovement(
        household_id=household_id, account_id=account_id,
        expected_date=expected_date, amount_pence=amount_pence,
        label=label, confidence=confidence, source_type=source,
        source_id=source_id)


def _drop_future(session: Session, source: ExpectedSource,
                 source_id: int, today: date) -> None:
    session.execute(
        delete(ExpectedMovement).where(
            ExpectedMovement.source_type == source,
            ExpectedMovement.source_id == source_id,
            ExpectedMovement.expected_date >= today))


def rematerialize_commitment(
    session: Session, c: RecurringCommitment, *,
    today: date | None = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Delete+regenerate FUTURE expected rows for one commitment."""
    today = today or date.today()
    horizon = today + timedelta(days=horizon_days)
    _drop_future(session, ExpectedSource.commitment, c.id, today)
    if not c.active:
        session.flush()
        return 0
    n = 0
    for d in occurrences(c, horizon):
        if d < today:
            continue
        session.add(_emit_row(
            c.household_id, account_id=c.account_id,
            expected_date=d, amount_pence=c.amount_pence or 0,
            label=c.name,
            confidence=(Confidence.estimated if c.formula
                        else Confidence.scheduled),
            source=ExpectedSource.commitment, source_id=c.id))
        n += 1
    session.flush()
    return n


def rematerialize_payroll_run(
    session: Session, run: PayrollRun, *,
    today: date | None = None,
) -> int:
    """Expected income on the run's pay date — estimated until the run
    is reconciled, scheduled after. Only future/mutable runs emit."""
    today = today or date.today()
    _drop_future(session, ExpectedSource.payroll_run, run.id, today)
    if run.status is PayrollRunStatus.corrected \
            or run.pay_date < today:
        session.flush()
        return 0
    confidence = (Confidence.scheduled
                  if run.status is PayrollRunStatus.reconciled
                  else Confidence.estimated)
    session.add(_emit_row(
        run.employment.household_id, account_id=None,
        expected_date=run.pay_date, amount_pence=run.net_pence,
        label=f"Salary — {run.employment.employer}",
        confidence=confidence, source=ExpectedSource.payroll_run,
        source_id=run.id))
    session.flush()
    return 1


def materialize_all(
    session: Session, household: Household, *,
    today: date | None = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Regenerate every source's future rows for a household."""
    today = today or date.today()
    n = 0
    for c in session.execute(
            select(RecurringCommitment).where(
                RecurringCommitment.household_id == household.id,
                RecurringCommitment.active.is_(True))
    ).scalars().all():
        n += rematerialize_commitment(session, c, today=today,
                                      horizon_days=horizon_days)
    for r in session.execute(
            select(PayrollRun).where(
                PayrollRun.employment.has(household_id=household.id))
    ).scalars().all():
        n += rematerialize_payroll_run(session, r, today=today)
    return n


def add_manual(session: Session, household_id: int, *,
               account_id: int | None, expected_date: date,
               amount_pence: int, label: str) -> ExpectedMovement:
    """A user-entered plan — its own source."""
    row = ExpectedMovement(
        household_id=household_id, account_id=account_id,
        expected_date=expected_date, amount_pence=amount_pence,
        label=label, confidence=Confidence.scheduled,
        source_type=ExpectedSource.manual, source_id=0)
    session.add(row)
    session.flush()
    row.source_id = row.id  # self-referential traceability
    session.flush()
    return row


def stream(session: Session, household_id: int, *,
           start: date, end: date,
           account_id: int | None = None) -> list[ExpectedMovement]:
    stmt = select(ExpectedMovement).where(
        ExpectedMovement.household_id == household_id,
        ExpectedMovement.expected_date >= start,
        ExpectedMovement.expected_date <= end)
    if account_id is not None:
        stmt = stmt.where(ExpectedMovement.account_id == account_id)
    return list(session.execute(
        stmt.order_by(ExpectedMovement.expected_date,
                      ExpectedMovement.id)).scalars().all())
