"""Issue #74 — per-account daily projection + safe-to-spend.

Projection = cleared ledger balance + Σ expected movements per day,
for ``days`` ahead. Deterministic: same ledger + same movement
stream → identical series.

Safe-to-spend = cleared balance − committed obligations *until the
next payday*. A payday is the next positive expected movement of
``payroll_run`` source strictly after ``from_date``; obligations
dated on or before it are committed against the current balance
(money due on payday itself must be covered — wages may not have
cleared). With no payroll expected, the whole horizon counts.
Expected movements are unique per (source, date, account) so
obligations are never counted twice.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.accounts import current_balance
from app.persistence.models import (
    Account,
    ExpectedMovement,
    ExpectedSource,
)

DEFAULT_HORIZON_DAYS = 90
MAX_HORIZON_DAYS = 731  # two years + leap day — bounds response size


@dataclass(frozen=True)
class DayPoint:
    date: date
    movements_pence: int   # net expected change on this date
    balance_pence: int     # projected cleared balance end-of-day


@dataclass(frozen=True)
class Projection:
    account_id: int
    start_balance_pence: int
    next_payday: date | None
    committed_until_payday_pence: int   # positive = money owed
    safe_to_spend_pence: int
    min_balance_pence: int              # lowest projected balance
    min_balance_date: date | None
    threshold_pence: int | None         # account.min_balance_pence
    below_threshold: bool
    days: list[DayPoint]


def _movement_rows(session: Session, account_id: int, start: date,
                   end: date) -> list[tuple[int, date, int]]:
    """Live expected movements in (start, end] as (id, date, pence)
    rows — the level scenario ops need to address individual items."""
    stmt = (
        select(ExpectedMovement.id,
               ExpectedMovement.expected_date,
               ExpectedMovement.amount_pence)
        .where(ExpectedMovement.account_id == account_id,
               ExpectedMovement.expected_date > start,
               ExpectedMovement.expected_date <= end,
               ExpectedMovement.dismissed_at.is_(None)))
    return [(int(r[0]), r[1], int(r[2])) for r in session.execute(stmt)]


def _aggregate(rows: list[tuple[date, int]]
               ) -> tuple[dict[date, int], dict[date, int]]:
    """Per-date flows: (net, negative-gross). Net drives the balance
    series; gross negatives drive committed obligations — a bill due
    ON payday must not be swallowed by the wage arriving the same
    date."""
    net: dict[date, int] = {}
    neg: dict[date, int] = {}
    for d, amt in rows:
        net[d] = net.get(d, 0) + amt
        if amt < 0:
            neg[d] = neg.get(d, 0) + amt
    return net, neg


def _movements(session: Session, account_id: int, start: date,
               end: date) -> tuple[dict[date, int], dict[date, int]]:
    return _aggregate(
        [(d, amt) for _, d, amt in
         _movement_rows(session, account_id, start, end)])


def _assemble(account: Account, balance: int,
              net: dict[date, int], negatives: dict[date, int],
              payday: date | None, today: date, end: date) -> Projection:
    committed_until = payday or end
    committed_pence = -sum(
        amt for d, amt in negatives.items() if d <= committed_until)

    series: list[DayPoint] = []
    running = balance
    lowest, lowest_date = balance, None
    cur = today
    while cur < end:
        cur += timedelta(days=1)
        delta = net.get(cur, 0)
        running += delta
        series.append(DayPoint(cur, delta, running))
        if running < lowest:
            lowest, lowest_date = running, cur

    threshold = account.min_balance_pence
    return Projection(
        account_id=account.id,
        start_balance_pence=balance,
        next_payday=payday,
        committed_until_payday_pence=committed_pence,
        safe_to_spend_pence=balance - committed_pence,
        min_balance_pence=lowest,
        min_balance_date=lowest_date,
        threshold_pence=threshold,
        below_threshold=(threshold is not None and lowest < threshold),
        days=series,
    )


def _next_payday(session: Session, account_id: int, today: date,
                 horizon_end: date) -> date | None:
    """Earliest expected payroll inflow strictly after ``today``."""
    return session.execute(
        select(func.min(ExpectedMovement.expected_date))
        .where(ExpectedMovement.account_id == account_id,
               ExpectedMovement.source_type == ExpectedSource.payroll_run,
               ExpectedMovement.amount_pence > 0,
               ExpectedMovement.expected_date > today,
               ExpectedMovement.expected_date <= horizon_end,
               ExpectedMovement.dismissed_at.is_(None))).scalar_one()


def project(session: Session, account: Account, days: int,
            from_date: date | None = None) -> Projection:
    """Daily projection + safe-to-spend for one account.

    ``days`` is clamped to ``MAX_HORIZON_DAYS``; ``from_date``
    defaults to today (injected for determinism in tests)."""
    days = max(1, min(days, MAX_HORIZON_DAYS))
    today = from_date or date.today()
    end = today + timedelta(days=days)

    movements, negatives = _movements(session, account.id, today, end)
    payday = _next_payday(session, account.id, today, end)
    balance = current_balance(session, account.id)
    return _assemble(account, balance, movements, negatives,
                     payday, today, end)
