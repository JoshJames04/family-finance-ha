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

from app.domain import outbox
from app.domain.money import Money
from app.persistence.models import (
    Account,
    AppMeta,
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


def ledger(session: Session, goal: SavingsGoal,
           today: date | None = None) -> list[Transaction]:
    """The contribution ledger — inbound transfer legs, dated."""
    return list(session.execute(
        select(Transaction).where(
            Transaction.account_id == goal.account_id,
            Transaction.amount_pence > 0,
            Transaction.transfer_id.is_not(None),
            Transaction.status != TransactionStatus.void,
            Transaction.date >= goal.start_date,
            Transaction.date <= (today or date.today()))
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
    if goal.target_date < today:
        return []
    need = remaining_pence(session, goal, today)
    if not need:
        return []
    months = _months_between(today, goal.target_date)
    per = -(-need // months)
    out: list[tuple[date, int]] = []
    for offset in range(1, months + 1):
        due = goal.target_date if offset == months else _add_months(today, offset)
        amount = min(per, need)
        out.append((due, amount))
        need -= amount
        if need == 0:
            break
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
    today = today or date.today()
    saved = contributed_pence(session, goal, today=today)
    return saved >= goal.target_pence or saved // _months_between(goal.start_date, today) >= req


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


def emit_contribution_milestones(session: Session, transaction: Transaction, *,
                                 today: date | None = None) -> None:
    """Emit actual threshold crossings when a real inbound leg is attributed.

    Called by contribution writers, never polling. The newly eligible credit
    is subtracted from authoritative progress to recover the preceding total.
    """
    today = today or date.today()
    if (transaction.transfer_id is None or transaction.amount_pence <= 0
            or transaction.status is TransactionStatus.void):
        return
    account = session.get(Account, transaction.account_id)
    if account is None:
        return
    for goal in session.scalars(select(SavingsGoal).where(
            SavingsGoal.account_id == account.id, SavingsGoal.active.is_(True),
            SavingsGoal.start_date <= transaction.date)):
        if transaction.date > today:
            from app.domain.financial_events import _observe

            # Seed effective-date progress now, so the normal durable scheduler
            # can see this real credit become eligible without replaying history.
            key = f"finance:goal-funding:{goal.id}"
            if session.get(AppMeta, key) is None:
                _observe(session, key, {
                    "owner": account.owner_profile_id,
                    "funded": contributed_pence(session, goal, today=today),
                })
            continue
        total = contributed_pence(session, goal, today=today)
        previous = total - transaction.amount_pence
        _emit_crossings(session, goal, account, previous, total, today)


def _emit_crossings(session: Session, goal: SavingsGoal, account: Account,
                    previous: int, total: int, today: date) -> None:
    if account.household_id != goal.household_id:
        return
    milestones = newly_crossed(previous, total, goal.target_pence)
    if not milestones:
        return
    funded = 0
    for credit in ledger(session, goal, today=today):
        funded += credit.amount_pence
        for milestone in list(milestones):
            if funded * 100 < goal.target_pence * milestone:
                continue
            outbox.emit(
                session, goal.household_id, "goal.milestone",
                entity_kind="savings_goal", entity_id=goal.id,
                payload={"source": "savings_goal", "state": str(milestone),
                         "date": credit.date.isoformat(),
                         "owner_profile_id": account.owner_profile_id,
                         "_scope_owner_profile_id": account.owner_profile_id},
                dedup_key=f"goal:{goal.id}:milestone:{milestone}")
            if milestone == 100:
                outbox.emit(
                    session, goal.household_id, "goal.completed",
                    entity_kind="savings_goal", entity_id=goal.id,
                    payload={"source": "savings_goal", "status": "completed",
                             "date": credit.date.isoformat(), "name": goal.name,
                             "amount_pence": funded,
                             "amount_gbp": str(Money.of_pence(funded).pounds_decimal()),
                             "target_pence": goal.target_pence, "currency": "GBP",
                             "_scope_owner_profile_id": account.owner_profile_id},
                    dedup_key=f"goal:{goal.id}:completed")
            milestones.remove(milestone)
        if not milestones:
            return


def evaluate_automation(session: Session, household_id: int, *,
                        today: date | None = None) -> None:
    """Canonical progress transitions and a durable three-day deadline window."""
    from app.domain.financial_events import _observe

    today = today or date.today()
    session.flush()
    for goal in session.scalars(select(SavingsGoal).where(
            SavingsGoal.household_id == household_id, SavingsGoal.active.is_(True))):
        account = session.get(Account, goal.account_id)
        if account is None or account.household_id != household_id or goal.start_date > today:
            continue
        state = progress(session, goal, today=today)
        key = f"finance:goal:{goal.id}"
        change = _observe(session, key, {
            "on_track": state.on_track, "owner": account.owner_profile_id,
        })
        funding = _observe(session, f"finance:goal-funding:{goal.id}", {
            "owner": account.owner_profile_id, "funded": state.contributed_pence,
        })
        previous_funded = funding[0].get("funded") if funding and funding[0] is not None else None
        if (isinstance(previous_funded, int)
                and funding is not None and funding[0] is not None
                and funding[0].get("owner") == account.owner_profile_id):
            _emit_crossings(session, goal, account, previous_funded, state.contributed_pence, today)
        payload: dict[str, object] = {
            "source": "savings_goal", "date": today.isoformat(), "name": goal.name,
            "amount_pence": state.contributed_pence,
            "amount_gbp": str(Money.of_pence(state.contributed_pence).pounds_decimal()),
            "target_pence": goal.target_pence, "remaining_pence": state.remaining_pence,
            "currency": "GBP",
            "_scope_owner_profile_id": account.owner_profile_id,
        }
        if goal.target_date is not None:
            payload["expected_date"] = goal.target_date.isoformat()
        if (state.remaining_pence > 0 and change is not None and change[0] is not None
                and change[0].get("owner") == account.owner_profile_id
                and change[0].get("on_track") is True and state.on_track is False):
            outbox.emit(
                session, household_id, "goal.behind_plan", entity_kind="savings_goal",
                entity_id=goal.id, payload={**payload, "status": "behind_plan"},
                dedup_key=f"{key}:behind:{change[1]}")
        if (goal.target_date is not None and state.remaining_pence > 0
                and 0 <= (goal.target_date - today).days <= 3):
            outbox.emit(
                session, household_id, "goal.target_approaching", entity_kind="savings_goal",
                entity_id=goal.id, payload={**payload, "status": "approaching", "lead_days": 3},
                dedup_key=f"{key}:approaching:{goal.target_date}:3")
