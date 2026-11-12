"""Savings goals + sinking funds (issue #91).

A goal targets a linked pot account. Its contribution ledger is
DERIVED from real inbound transfer legs — no separate table, so the
ledger and the account view can never disagree. Dated goals are
sinking funds: the monthly need (target − contributed) ÷ remaining
months materialises as `estimated` expected movements.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    GoalType,
    SavingsGoal,
    Transaction,
    TransactionStatus,
)


class GoalError(Exception):
    """Domain validation failure → API maps to 409."""


def _months_between(start: date, end: date) -> int:
    """Whole calendar months start→end, clamped ≥1."""
    m = (end.year - start.year) * 12 + (end.month - start.month)
    return max(1, m)


def contributed_pence(session: Session, goal: SavingsGoal,
                      since: date | None = None, today: date | None = None) -> int:
    """Real inbound transfer legs into the goal account."""
    q = select(func.coalesce(func.sum(Transaction.amount_pence), 0)
               ).where(
        Transaction.account_id == goal.account_id,
        Transaction.amount_pence > 0,
        Transaction.transfer_id.is_not(None),
        Transaction.status != TransactionStatus.void,
        Transaction.date >= goal.start_date,
        Transaction.date <= (today or date.today()))
    if since is not None:
        q = q.where(Transaction.date >= since)
    return int(session.execute(q).scalar_one())


def ledger(session: Session, goal: SavingsGoal) -> list[Transaction]:
    """The contribution ledger — inbound transfer legs, dated."""
    return list(session.execute(
        select(Transaction).where(
            Transaction.account_id == goal.account_id,
            Transaction.amount_pence > 0,
            Transaction.transfer_id.is_not(None),
            Transaction.status != TransactionStatus.void,
            Transaction.date >= goal.start_date,
            Transaction.date <= date.today())
        .order_by(Transaction.date, Transaction.id)).scalars())


def remaining_pence(session: Session, goal: SavingsGoal,
                    today: date | None = None) -> int:
    return max(0, goal.target_pence - contributed_pence(session, goal, today=today))


def monthly_need(session: Session, goal: SavingsGoal,
                 today: date | None = None) -> int | None:
    """Sinking-fund payment per month from today to the target date —
    (target − contributed) ÷ months remaining, rounded UP so the goal
    is met. ``None`` for undated goals."""
    if goal.target_date is None:
        return None
    today = today or date.today()
    months = _months_between(today, goal.target_date)
    need = remaining_pence(session, goal, today)
    return -(-need // months)  # ceiling division


def sinking_schedule(session: Session, goal: SavingsGoal, *,
                     today: date | None = None) -> list[tuple[date, int]]:
    """Monthly (date, pence) pairs from next month to target — what the
    expected-movement stream materialises. Earlier months may need to
    catch up if contributions are behind."""
    if goal.target_date is None:
        return []
    today = today or date.today()
    need = remaining_pence(session, goal, today)
    months = _months_between(today, goal.target_date)
    per = -(-need // months)
    out: list[tuple[date, int]] = []
    y, m = today.year, today.month
    for _ in range(months):
        m += 1
        if m > 12:
            m, y = 1, y + 1
        out.append((date(y, m, 1), per))
    return out


@dataclass(frozen=True)
class GoalProgress:
    contributed_pence: int
    remaining_pence: int
    monthly_need_pence: int | None
    pct: int  # whole-percent funded
    average_rate_pence: int
    projected_date: date | None
    on_track: bool | None


def progress(session: Session, goal: SavingsGoal,
             today: date | None = None) -> GoalProgress:
    c = contributed_pence(session, goal, today=today)
    return GoalProgress(
        contributed_pence=c,
        remaining_pence=max(0, goal.target_pence - c),
        monthly_need_pence=monthly_need(session, goal, today),
        pct=min(100, c * 100 // goal.target_pence),
        average_rate_pence=average_monthly_rate(session, goal, today),
        projected_date=projected_completion(session, goal, today),
        on_track=on_track(session, goal, today))


# Sinking funds whose contributions are periodic expectations — these
# materialise into the expected stream; ad-hoc goals do not.
SINKING_TYPES = {GoalType.annual_reserve, GoalType.christmas,
                 GoalType.holiday, GoalType.emergency}


# ---------- projections (issue #92) --------------------------------

MILESTONES = (25, 50, 75, 100)


def _add_months(d: date, months: int) -> date:
    m = d.month + months
    y = d.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1)


def average_monthly_rate(session: Session, goal: SavingsGoal,
                         today: date | None = None) -> int:
    """Contribution velocity: total ÷ months since goal start."""
    today = today or date.today()
    months = _months_between(goal.start_date, today)
    return contributed_pence(session, goal, today=today) // months


def projected_completion(session: Session, goal: SavingsGoal,
                         today: date | None = None) -> date | None:
    """When the goal lands at the current contribution rate — the 1st
    of the month it completes. ``None`` when the rate is zero
    (unknowable) or the goal is already funded."""
    today = today or date.today()
    need = remaining_pence(session, goal, today)
    if need == 0:
        return today  # funded
    rate = average_monthly_rate(session, goal, today)
    if rate <= 0:
        return None
    months = -(-need // rate)  # ceil
    return _add_months(today, months)


def required_monthly_rate(session: Session, goal: SavingsGoal,
                          today: date | None = None) -> int | None:
    """The pace needed to hit a dated target: full target ÷ total
    months in the window (start→target), not just the remainder."""
    if goal.target_date is None:
        return None
    months = _months_between(goal.start_date, goal.target_date)
    return -(-goal.target_pence // months)


def on_track(session: Session, goal: SavingsGoal,
             today: date | None = None) -> bool | None:
    """Is the actual contribution rate ≥ the required rate? ``None``
    for undated goals (nothing to be on-track *for*)."""
    req = required_monthly_rate(session, goal, today)
    if req is None:
        return None
    return average_monthly_rate(session, goal, today) >= req


def crossed_milestones(session: Session, goal: SavingsGoal) -> list[int]:
    """Percentage milestones the funded pct has reached — Epic 15
    diffs these for notifications."""
    pct = contributed_pence(session, goal) * 100 // goal.target_pence
    return [m for m in MILESTONES if pct >= m]


def newly_crossed(prev_pence: int, new_pence: int,
                  target_pence: int) -> list[int]:
    """Milestones crossed by a single contribution — the notification
    trigger Epic 15 consumes."""
    before = prev_pence * 100 // target_pence
    after = new_pence * 100 // target_pence
    return [m for m in MILESTONES if before < m <= after]
