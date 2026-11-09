"""Issue #111 — monthly materialised aggregates + time-series.

Net worth = Σ account balances (ledger-derived, opening row included)
+ signed loan outstanding: ``we_owe`` loans are liabilities (−),
``they_owe`` loans are assets (+). Aggregates are materialised per
month and are rebuildable identically from raw data — ``rebuild`` is
the only writer.
"""
from __future__ import annotations

import calendar
from datetime import date

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.domain import loans as loan_domain
from app.domain.reporting import external_only
from app.persistence.models import (
    Account,
    Loan,
    LoanDirection,
    MonthlyAggregate,
    Transaction,
    TransactionStatus,
)


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month,
                calendar.monthrange(d.year, d.month)[1])


def months_between(start: date, end: date) -> list[date]:
    """First-of-month dates spanning ``start``..``end`` inclusive."""
    out: list[date] = []
    cur = _month_start(start)
    while cur <= end:
        out.append(cur)
        cur = (date(cur.year + 1, 1, 1) if cur.month == 12
               else date(cur.year, cur.month + 1, 1))
    return out


def net_worth_at(session: Session, household_id: int,
                 on: date) -> int:
    """Σ account balances at ``on`` + signed loan outstanding."""
    bal = session.execute(
        select(func.coalesce(func.sum(Transaction.amount_pence), 0))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.status != TransactionStatus.void,
               Transaction.date <= on)).scalar_one()
    total = int(bal)
    for loan in session.execute(
            select(Loan).where(Loan.household_id == household_id,
                               Loan.start_date <= on)).scalars():
        out = loan_domain.outstanding(session, loan, on=on)
        total += -out if loan.direction == LoanDirection.we_owe \
            else out
    return total


_MONTH_EXPR = func.strftime("%Y-%m", Transaction.date)

# 50 years — beyond any plausible household dataset, and a hard bound
# on rebuild cost regardless of pathological transaction dates.
MAX_REBUILD_MONTHS = 600


def _grouped_months(session: Session, household_id: int,
                    external: bool) -> dict[date, tuple[int, int]]:
    """One grouped ledger pass: month → (income_pence, expense_pence).
    ``external=True`` applies the #90 transfer-leg exclusion."""
    stmt = (
        select(
            _MONTH_EXPR,
            func.coalesce(func.sum(
                case((Transaction.amount_pence > 0,
                      Transaction.amount_pence), else_=0)), 0),
            func.coalesce(func.sum(
                case((Transaction.amount_pence < 0,
                      -Transaction.amount_pence), else_=0)), 0))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.status != TransactionStatus.void))
    if external:
        stmt = external_only(stmt)
    out: dict[date, tuple[int, int]] = {}
    for ym, inc, exp in session.execute(stmt.group_by(_MONTH_EXPR)):
        out[date(int(ym[:4]), int(ym[5:]), 1)] = (int(inc), int(exp))
    return out


def _monthly_net(session: Session, household_id: int
                 ) -> dict[date, int]:
    """Signed per-month ledger total INCLUDING transfer legs — balance
    accumulation, same basis as ``net_worth_at``/``current_balance``
    (legs dated either side of a month boundary are in-flight money)."""
    stmt = (
        select(_MONTH_EXPR,
               func.coalesce(func.sum(Transaction.amount_pence), 0))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.status != TransactionStatus.void)
        .group_by(_MONTH_EXPR))
    return {date(int(ym[:4]), int(ym[5:]), 1): int(net)
            for ym, net in session.execute(stmt)}


def rebuild(session: Session, household_id: int,
            through: date | None = None) -> int:
    """Recompute every aggregate month from earliest transaction to
    ``through`` (default: latest transaction, else nothing). Returns
    the number of rows written. Stale rows outside the range are left
    untouched only if they fall within ``earliest..through`` — rows
    for months with no longer any activity are deleted.

    Batched: two grouped ledger passes feed income/expense/savings and
    the cumulative balance; loans are evaluated per month-end (few
    rows in practice)."""
    through = through or session.execute(
        select(func.max(Transaction.date))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id)).scalar_one()
    if through is None:
        session.execute(
            delete(MonthlyAggregate).where(
                MonthlyAggregate.household_id == household_id))
        return 0
    earliest = session.execute(
        select(func.min(Transaction.date))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id)).scalar_one()
    months = months_between(
        earliest, _month_start(through))[:MAX_REBUILD_MONTHS]
    if not months:
        return 0
    flows = _grouped_months(session, household_id, external=True)
    nets = _monthly_net(session, household_id)
    loans = session.execute(
        select(Loan).where(
            Loan.household_id == household_id,
            Loan.start_date <= _month_end(months[-1]))).scalars().all()
    balance = 0
    written = 0
    for m in months:
        inc, exp = flows.get(m, (0, 0))
        balance += nets.get(m, 0)
        end = _month_end(m)
        nw = balance + sum(
            (-o if ln.direction == LoanDirection.we_owe else o)
            for ln in loans if ln.start_date <= end
            for o in [loan_domain.outstanding(session, ln, on=end)])
        figs = {
            "income_pence": inc,
            "expense_pence": exp,
            "savings_pence": inc - exp,
            "net_worth_pence": nw,
        }
        stmt = insert(MonthlyAggregate).values(
            household_id=household_id, month=m, **figs)
        stmt = stmt.on_conflict_do_update(
            index_elements=["household_id", "month"], set_=figs)
        session.execute(stmt)
        written += 1
    # drop rows outside the rebuilt window — e.g. a trailing month
    # whose transactions were deleted
    session.execute(
        delete(MonthlyAggregate).where(
            MonthlyAggregate.household_id == household_id,
            (MonthlyAggregate.month < months[0]) |
            (MonthlyAggregate.month > months[-1])))
    return written


def series(session: Session, household_id: int,
           months: int) -> list[MonthlyAggregate]:
    """Most recent ``months`` aggregate rows, chronological."""
    rows = session.execute(
        select(MonthlyAggregate)
        .where(MonthlyAggregate.household_id == household_id)
        .order_by(MonthlyAggregate.month.desc())
        .limit(months)).scalars().all()
    return list(reversed(rows))
