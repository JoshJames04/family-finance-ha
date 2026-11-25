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
from app.domain import splits
from app.domain.reporting import expense_leg, external_category_amounts, income_leg
from app.persistence.models import (
    Account,
    Loan,
    LoanDirection,
    MonthlyAggregate,
    Repayment,
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
                 on: date, *, account_ids: list[int] | None = None,
                 loan_ids: list[int] | None = None) -> int:
    """Σ account balances at ``on`` + signed loan outstanding."""
    bal = session.execute(
        select(func.coalesce(func.sum(Transaction.amount_pence), 0))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.status != TransactionStatus.void,
               *([Account.id.in_(account_ids)] if account_ids is not None else []),
               Transaction.date <= on)).scalar_one()
    total = int(bal)
    for loan in session.execute(
            select(Loan).where(Loan.household_id == household_id,
                               *([Loan.id.in_(loan_ids)] if loan_ids is not None else []),
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
                    external: bool, through: date, account_ids: list[int] | None = None,
                    ) -> dict[date, tuple[int, int]]:
    """One grouped ledger pass: month → (income_pence, expense_pence).
    ``external=True`` applies the #90 transfer-leg exclusion."""
    amounts = (external_category_amounts(
        Account.household_id == household_id,
        *([Account.id.in_(account_ids)] if account_ids is not None else []),
        Transaction.status != TransactionStatus.void) if external else
        splits.effective_category_amounts(
            Account.household_id == household_id,
            *([Account.id.in_(account_ids)] if account_ids is not None else []),
            Transaction.status != TransactionStatus.void))
    month = func.strftime("%Y-%m", amounts.c.date)
    income = income_leg(amounts) if external else amounts.c.amount_pence > 0
    expense = expense_leg(amounts) if external else amounts.c.amount_pence < 0
    rows = session.execute(select(
        month,
        func.coalesce(func.sum(case((income,
                                    amounts.c.amount_pence), else_=0)), 0),
        func.coalesce(func.sum(case((expense,
                                    -amounts.c.amount_pence), else_=0)), 0),
    ).where(amounts.c.date <= through).group_by(month))
    return {date(int(ym[:4]), int(ym[5:]), 1): (int(inc), int(exp))
            for ym, inc, exp in rows}


def _monthly_net(session: Session, household_id: int, through: date,
                 account_ids: list[int] | None = None) -> dict[date, int]:
    """Signed per-month ledger total INCLUDING transfer legs — balance
    accumulation, same basis as ``net_worth_at``/``current_balance``
    (legs dated either side of a month boundary are in-flight money)."""
    stmt = (
        select(_MONTH_EXPR,
               func.coalesce(func.sum(Transaction.amount_pence), 0))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               *([Account.id.in_(account_ids)] if account_ids is not None else []),
               Transaction.status != TransactionStatus.void, Transaction.date <= through)
        .group_by(_MONTH_EXPR))
    return {date(int(ym[:4]), int(ym[5:]), 1): int(net)
            for ym, net in session.execute(stmt)}


def _figures(session: Session, household_id: int,
             through: date | None = None, *, account_ids: list[int] | None = None,
             loan_ids: list[int] | None = None, limit: int = MAX_REBUILD_MONTHS,
             ) -> list[tuple[date, dict[str, int]]]:
    """Fresh monthly economics and raw ledger stock, without materialising rows."""
    cutoff = through or date.today()
    posting_start, posting_end = session.execute(select(
        func.min(Transaction.date), func.max(Transaction.date)).join(Account).where(
            Account.household_id == household_id, Transaction.date <= cutoff,
            Transaction.status != TransactionStatus.void,
            *([Account.id.in_(account_ids)] if account_ids is not None else []))).one()
    loans = session.scalars(select(Loan).where(
        Loan.household_id == household_id, Loan.start_date <= cutoff,
        *([Loan.id.in_(loan_ids)] if loan_ids is not None else []))).all()
    starts = [loan.start_date for loan in loans]
    if posting_start is not None:
        starts.append(posting_start)
    if not starts:
        return []
    # Outstanding loan principal and interest continue to affect net worth in
    # quiet months. Closed loans still contribute dated repayments to history.
    window_end = cutoff
    if through is None and not any(loan.active for loan in loans):
        last_repayment = session.scalar(select(func.max(Repayment.date)).where(
            Repayment.loan_id.in_([loan.id for loan in loans]), Repayment.date <= cutoff))
        window_end = max(d for d in [*starts, posting_end, last_repayment] if d is not None)
    earliest = _month_start(min(starts))
    last_month = _month_start(window_end)
    earliest_index = (earliest.year - 1) * 12 + earliest.month - 1
    last_index = (last_month.year - 1) * 12 + last_month.month - 1
    first_index = max(earliest_index, last_index - limit + 1)
    months = months_between(date(first_index // 12 + 1, first_index % 12 + 1, 1), last_month)
    flows = _grouped_months(session, household_id, external=True, through=cutoff,
                            account_ids=account_ids)
    nets = _monthly_net(session, household_id, cutoff, account_ids)
    balance = sum(net for month, net in nets.items() if month < months[0])
    result = []
    for month in months:
        inc, exp = flows.get(month, (0, 0))
        balance += nets.get(month, 0)
        end = min(cutoff, _month_end(month))
        net_worth = balance + sum(
            -outstanding if loan.direction == LoanDirection.we_owe else outstanding
            for loan in loans if loan.start_date <= end
            for outstanding in [loan_domain.outstanding(session, loan, on=end)])
        result.append((month, {
            "income_pence": inc, "expense_pence": exp,
            "savings_pence": inc - exp, "net_worth_pence": net_worth,
        }))
    return result


def rebuild(session: Session, household_id: int,
            through: date | None = None) -> int:
    """Explicitly materialise fresh figures; normal reads require no rebuild."""
    rows = _figures(session, household_id, through)
    if not rows:
        session.execute(delete(MonthlyAggregate).where(
            MonthlyAggregate.household_id == household_id))
        return 0
    for month, figures in rows:
        stmt = insert(MonthlyAggregate).values(
            household_id=household_id, month=month, **figures)
        session.execute(stmt.on_conflict_do_update(
            index_elements=["household_id", "month"], set_=figures))
    session.execute(delete(MonthlyAggregate).where(
        MonthlyAggregate.household_id == household_id,
        (MonthlyAggregate.month < rows[0][0]) | (MonthlyAggregate.month > rows[-1][0])))
    return len(rows)


def series(session: Session, household_id: int,
           months: int, *, account_ids: list[int] | None = None,
           loan_ids: list[int] | None = None) -> list[MonthlyAggregate]:
    """Fresh chronological monthly values; never write or trust stale cache rows."""
    with session.no_autoflush:
        rows = _figures(session, household_id, account_ids=account_ids, loan_ids=loan_ids,
                        limit=max(1, min(months, 120)))
    return [MonthlyAggregate(household_id=household_id, month=month, **figures)
            for month, figures in rows]
