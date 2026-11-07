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

from app.domain.accounts import active_accounts, current_balance
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


def _movements(session: Session, account_id: int, start: date,
               end: date) -> tuple[dict[date, int], dict[date, int]]:
    """Per-date flows in (start, end]: (net, negative-gross). Net
    drives the balance series; gross negatives drive committed
    obligations — a bill due ON payday must not be swallowed by the
    wage arriving the same date."""
    stmt = (
        select(ExpectedMovement.expected_date,
               ExpectedMovement.amount_pence)
        .where(ExpectedMovement.account_id == account_id,
               ExpectedMovement.expected_date > start,
               ExpectedMovement.expected_date <= end,
               ExpectedMovement.dismissed_at.is_(None)))
    net: dict[date, int] = {}
    neg: dict[date, int] = {}
    for d, amt in session.execute(stmt):
        amt = int(amt)
        net[d] = net.get(d, 0) + amt
        if amt < 0:
            neg[d] = neg.get(d, 0) + amt
    return net, neg


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
    committed_until = payday or end
    committed_pence = -sum(
        amt for d, amt in negatives.items() if d <= committed_until)

    balance = current_balance(session, account.id)
    series: list[DayPoint] = []
    running = balance
    lowest, lowest_date = balance, None
    cur = today
    while cur < end:
        cur += timedelta(days=1)
        delta = movements.get(cur, 0)
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


@dataclass(frozen=True)
class SourceSuggestion:
    account_id: int
    name: str
    available_pence: int    # surplus above own floor on the dip date


@dataclass(frozen=True)
class OverdraftWarning:
    account_id: int
    name: str
    first_dip_date: date
    floor_pence: int            # threshold or 0 when unset
    min_balance_pence: int
    deficit_pence: int          # floor − min balance
    suggested_sources: list[SourceSuggestion]


def _floor(account: Account) -> int:
    return account.min_balance_pence or 0


def _balance_on(p: Projection, on: date) -> int:
    """Projected balance at ``on`` — the day's own value, or the last
    projected day when ``on`` sits inside the window."""
    for d in p.days:
        if d.date == on:
            return d.balance_pence
        if d.date > on:
            break
    return p.days[-1].balance_pence if p.days else p.start_balance_pence


def warnings(session: Session, household_id: int, days: int = 90,
             from_date: date | None = None,
             ) -> list[OverdraftWarning]:
    """Overdraft/minimum-balance warnings per account (issue #75).

    A warning fires when the projected series dips below the account's
    floor (``min_balance_pence`` threshold, else zero). Suggested
    rescue sources are the other active household accounts ordered by
    surplus-above-their-own-floor on the dip date — a multi-account
    chain until the deficit is covered.

    Purely computed: warnings resolve automatically when the ledger or
    movement stream changes — nothing is persisted.
    """
    accounts = active_accounts(session, household_id)
    projections = {a.id: project(session, a, days, from_date)
                   for a in accounts}
    out: list[OverdraftWarning] = []
    for a in accounts:
        floor = _floor(a)
        p = projections[a.id]
        dip = next((d for d in p.days if d.balance_pence < floor), None)
        if dip is None:
            continue
        deficit = floor - p.min_balance_pence
        sources: list[SourceSuggestion] = []
        remaining = deficit
        others = sorted(
            (b for b in accounts if b.id != a.id),
            key=lambda b: -(
                _balance_on(projections[b.id], dip.date) - _floor(b)))
        for b in others:
            if remaining <= 0:
                break
            avail = (_balance_on(projections[b.id], dip.date)
                     - _floor(b))
            if avail <= 0:
                continue
            sources.append(SourceSuggestion(b.id, b.name, avail))
            remaining -= avail
        out.append(OverdraftWarning(
            account_id=a.id, name=a.name, first_dip_date=dip.date,
            floor_pence=floor, min_balance_pence=p.min_balance_pence,
            deficit_pence=deficit, suggested_sources=sources))
    return out
