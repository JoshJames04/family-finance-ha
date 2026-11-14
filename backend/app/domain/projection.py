"""Issue #74 — per-account daily projection + safe-to-spend.

Projection = ledger balance + Σ signed outstanding movements per day,
for ``days`` ahead. Deterministic: same ledger + same movement
stream → identical series.

Safe-to-spend = cleared balance − committed obligations *until the
next payday*. A payday is the next positive expected movement of
``payroll_run`` or ``employment`` source strictly after ``from_date``; obligations
dated on or before it are committed against the current balance
(money due on payday itself must be covered — wages may not have
cleared). With no payroll expected, the whole horizon counts.
Expected movements are unique per (source, date, account) so
obligations are never counted twice.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.accounts import active_accounts, current_balance
from app.domain.contributions import matched_totals
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
                   end: date, *, payday_only: bool = False) -> list[tuple[int, date, int]]:
    """Outstanding movements in (start, end] as (id, date, signed pence)
    rows — the level scenario ops need to address individual items."""
    stmt = (
        select(ExpectedMovement)
        .where(ExpectedMovement.account_id == account_id,
               ExpectedMovement.expected_date > start,
               ExpectedMovement.expected_date <= end,
               ExpectedMovement.dismissed_at.is_(None)))
    if payday_only:
        stmt = stmt.where(ExpectedMovement.source_type.in_(
            [ExpectedSource.payroll_run, ExpectedSource.employment]),
            ExpectedMovement.amount_pence > 0)
    rows = []
    with session.no_autoflush:
        movements = list(session.scalars(stmt))
        totals = matched_totals(session, [movement.id for movement in movements])
        for movement in movements:
            remaining = max(0, abs(movement.amount_pence) - totals[movement.id])
            if remaining:
                rows.append((movement.id, movement.expected_date,
                             remaining if movement.amount_pence > 0 else -remaining))
    return rows


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


def _payday_dates(session: Session, account_id: int, start: date,
                  end: date) -> list[date]:
    """Baseline outstanding payroll dates; scenario operations never redefine them."""
    return sorted(d for _, d, _ in _movement_rows(
        session, account_id, start, end, payday_only=True))


def _next_payday(session: Session, account_id: int, today: date,
                 horizon_end: date) -> date | None:
    """Earliest outstanding payroll inflow strictly after ``today``."""
    return min(_payday_dates(session, account_id, today, horizon_end), default=None)


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
