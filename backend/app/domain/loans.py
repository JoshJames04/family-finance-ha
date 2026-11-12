"""Loans with a derived outstanding balance (issue #93).

Outstanding is always computed — principal + accrued simple interest
− validated repayments — never stored. Repayments can link to real
transactions; schedule-vs-actual drift compares cumulative expected
payments to cumulative actuals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import Loan, Repayment, Transaction


class LoanError(Exception):
    """Domain validation failure → API maps to 409."""


def accrued_interest(loan: Loan, on: date) -> int:
    """Simple interest on the original principal:
    principal × bps/10000 × days/365. ``0`` when unconfigured."""
    if not loan.interest_bps_pa:
        return 0
    days = max(0, (on - loan.start_date).days)
    return loan.principal_pence * loan.interest_bps_pa * days \
        // (10_000 * 365)


def repaid_pence(session: Session, loan: Loan,
                 before: date | None = None) -> int:
    q = select(func.coalesce(func.sum(Repayment.amount_pence), 0)
               ).where(Repayment.loan_id == loan.id)
    if before is not None:
        q = q.where(Repayment.date <= before)
    return int(session.execute(q).scalar_one())


def outstanding(session: Session, loan: Loan,
                on: date | None = None) -> int:
    """principal + accrued interest − repayments to date, floored at
    zero. Recomputes correctly after any repayment add/remove."""
    on = on or date.today()
    due = loan.principal_pence + accrued_interest(loan, on)
    return max(0, due - repaid_pence(session, loan, on))


def expected_to_date(loan: Loan, on: date) -> int:
    """Cumulative expected repayments: monthly expectation × whole
    months elapsed since start. ``0`` when no schedule configured."""
    if not loan.expected_payment_pence:
        return 0
    months = ((on.year - loan.start_date.year) * 12
              + (on.month - loan.start_date.month))
    return max(0, months) * loan.expected_payment_pence


@dataclass(frozen=True)
class Drift:
    expected_pence: int
    actual_pence: int
    drift_pence: int  # positive = behind schedule


def schedule_drift(session: Session, loan: Loan,
                   on: date | None = None) -> Drift:
    """Schedule vs actual: positive drift means the household is
    behind the repayment expectation."""
    on = on or date.today()
    expected = expected_to_date(loan, on)
    actual = repaid_pence(session, loan, on)
    return Drift(expected_pence=expected, actual_pence=actual,
                 drift_pence=expected - actual)


@dataclass(frozen=True)
class Projection:
    months: int | None          # None = never pays off on this rate
    payoff_date: date | None


def projected_payoff(
    session: Session, loan: Loan, *,
    extra_monthly_pence: int = 0, on: date | None = None,
    max_months: int = 600,
) -> Projection:
    """Simulated debt-free date at expected + extra per month (#94).

    Each simulated month m: balance = principal + interest accrued to
    then − repayments to date − (expected + extra)×m. Payoff is the
    first month at or below zero. ``None`` months means the rate
    never covers the principal."""
    on = on or date.today()
    rate = (loan.expected_payment_pence or 0) + extra_monthly_pence
    if rate <= 0:
        return Projection(months=None, payoff_date=None)
    base = repaid_pence(session, loan, on)
    anchor = date(on.year, on.month, 1)
    for m in range(1, max_months + 1):
        future = date(anchor.year + (anchor.month - 1 + m) // 12,
                      (anchor.month - 1 + m) % 12 + 1, 1)
        due = (loan.principal_pence + accrued_interest(loan, future)
               - base - rate * m)
        if due <= 0:
            return Projection(months=m, payoff_date=future)
    return Projection(months=None, payoff_date=None)


def repayment_balances(
    session: Session, loan: Loan,
) -> list[tuple[Repayment, int]]:
    """Repayment history with running outstanding after each entry
    (#94) — balance recomputed at each repayment's own date."""
    out: list[tuple[Repayment, int]] = []
    repaid = 0
    for r in sorted(loan.repayments,
                    key=lambda x: (x.date, x.id)):
        repaid += r.amount_pence
        due = loan.principal_pence + accrued_interest(loan, r.date) - repaid
        out.append((r, max(0, due)))
    return out


def link_repayment(session: Session, repayment: Repayment,
                   tx: Transaction) -> Repayment:
    """Bind a repayment to the real transaction it represents."""
    loan = repayment.loan or session.get(Loan, repayment.loan_id)
    if loan is None or tx.account.household_id != loan.household_id:
        raise LoanError("transaction belongs to another household")
    with session.no_autoflush:
        used = session.scalar(select(Repayment.id).where(
            Repayment.transaction_id == tx.id,
            Repayment.id != repayment.id).limit(1))
    if used is not None:
        raise LoanError("transaction already attributed to a repayment")
    expected_sign = -1 if loan.direction.value == "we_owe" else 1
    if (tx.status.value == "void" or tx.transfer_id is not None
            or tx.amount_pence * expected_sign != repayment.amount_pence):
        raise LoanError("repayment must match an eligible transaction amount and direction")
    repayment.transaction_id = tx.id
    return repayment
