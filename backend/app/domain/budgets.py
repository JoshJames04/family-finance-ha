"""Budget lifecycle and actuals (issue #57).

draft → approved → locked. Approval freezes line originals; revisions
are only legal while approved; locked budgets (explicitly or via a
locked reporting period covering the range) are read-only.

Overlapping budgets coexist freely — actuals are computed per-budget by
date range, so a transaction is attributed to each budget independently
and never double-counted *within* a budget.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    ReportingPeriod,
    Transaction,
    TransactionStatus,
)


class BudgetError(ValueError):
    """Domain rule violation on budget operations."""


def period_covers_locked(session: Session, household_id: int,
                         start: date, end: date) -> bool:
    """True when a locked reporting period fully covers [start, end]."""
    return session.execute(
        select(func.count(ReportingPeriod.id)).where(
            ReportingPeriod.household_id == household_id,
            ReportingPeriod.locked.is_(True),
            ReportingPeriod.start_date <= start,
            ReportingPeriod.end_date >= end)
    ).scalar_one() > 0


def is_read_only(session: Session, budget: Budget) -> bool:
    return (budget.status == BudgetStatus.locked
            or period_covers_locked(session, budget.household_id,
                                    budget.start_date, budget.end_date))


def _assert_mutable(session: Session, budget: Budget) -> None:
    if is_read_only(session, budget):
        raise BudgetError("budget is locked")


def create_budget(
    session: Session, household_id: int, name: str,
    period_type: BudgetPeriodType, start: date, end: date,
    owner_profile_id: int | None = None,
) -> Budget:
    if end < start:
        raise BudgetError("end_date must be on/after start_date")
    b = Budget(household_id=household_id,
               owner_profile_id=owner_profile_id, name=name,
               period_type=period_type, start_date=start, end_date=end,
               status=BudgetStatus.draft)
    session.add(b)
    session.flush()
    return b


def add_line(session: Session, budget: Budget, category_id: int,
             side: BudgetLineSide, amount_pence: int) -> BudgetLine:
    _assert_mutable(session, budget)
    if budget.status != BudgetStatus.draft:
        raise BudgetError("lines can only be added while draft")
    if amount_pence < 0:
        raise BudgetError("line amounts are unsigned; side carries sign")
    line = BudgetLine(budget_id=budget.id, category_id=category_id,
                      side=side, original_pence=amount_pence)
    session.add(line)
    session.flush()
    return line


def approve(session: Session, budget: Budget) -> Budget:
    _assert_mutable(session, budget)
    if budget.status != BudgetStatus.draft:
        raise BudgetError("only draft budgets can be approved")
    if not budget.lines:
        raise BudgetError("cannot approve a budget with no lines")
    budget.status = BudgetStatus.approved
    session.flush()
    return budget


def revise_line(session: Session, line: BudgetLine,
                amount_pence: int) -> BudgetLine:
    budget = line.budget
    _assert_mutable(session, budget)
    if budget.status != BudgetStatus.approved:
        raise BudgetError("revisions only while approved")
    if amount_pence < 0:
        raise BudgetError("line amounts are unsigned")
    line.revised_pence = amount_pence
    session.flush()
    return line


def lock(session: Session, budget: Budget) -> Budget:
    if budget.status != BudgetStatus.approved:
        raise BudgetError("only approved budgets can be locked")
    budget.status = BudgetStatus.locked
    session.flush()
    return budget


def effective_pence(line: BudgetLine) -> int:
    """Revised value if present, else the frozen original."""
    return (line.revised_pence if line.revised_pence is not None
            else line.original_pence)


def actuals_for_budget(session: Session, budget: Budget) -> dict[int, int]:
    """Per-line actual totals (expense lines: spend as positive pence;
    income lines: income as positive pence). Overlapping budgets each
    see the same underlying transactions — attribution is per-budget,
    so nothing is double-counted inside a single budget."""
    out: dict[int, int] = {}
    for line in budget.lines:
        q = select(func.coalesce(func.sum(Transaction.amount_pence), 0)
                   ).where(
            Transaction.category_id == line.category_id,
            Transaction.date >= budget.start_date,
            Transaction.date <= budget.end_date,
            Transaction.status != TransactionStatus.void,
        )
        total = int(session.execute(q).scalar_one())
        # expense lines measure outflow (negative ledger values);
        # income lines measure inflow (positive values)
        out[line.id] = -total if line.side == BudgetLineSide.expense \
            else total
    return out
