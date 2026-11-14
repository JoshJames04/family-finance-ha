"""Issue #110 — deterministic report generation.

A report is a document of sections, each holding figures. Every figure
carries a ``drill`` — the endpoint + query params that reproduce the
exact record set the figure was derived from — so every number is
traceable to its records. Reports contain no timestamps and every
collection is explicitly ordered, so re-running a report on unchanged
data is byte-identical.

Internal transfer legs are summarised in their own section and never
appear in income/expense figures (the #90 double-counting contract
via :func:`reporting.external_only`).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.domain import budgets as budget_domain
from app.domain import splits
from app.domain.reporting import (
    expense_leg,
    external_category_amounts,
    income_leg,
    internal_category_amounts,
)
from app.persistence.models import (
    Account,
    Budget,
    Category,
    Transaction,
    TransactionStatus,
)

TX = "/api/v1/transactions"


def _drill(**params: Any) -> dict[str, Any]:
    return {"endpoint": TX, "params": {
        k: (v.isoformat() if isinstance(v, date) else v)
        for k, v in {"exclude_void": True, "exclude_opening": True,
                     "internal": "exclude", **params}.items() if v is not None}}


def _base(
        household_id: int, start: date, end: date,
        account_ids: list[int] | None = None
) -> Select[Any]:
    return (
        select(func.coalesce(func.sum(Transaction.amount_pence), 0))
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Account.household_id == household_id,
            Transaction.status != TransactionStatus.void,
            Transaction.date >= start,
            Transaction.date <= end,
            *([Account.id.in_(account_ids)] if account_ids is not None else []),
            ~((Transaction.source == "system") & Transaction.external_ref.like("open:%"))
            | Transaction.external_ref.is_(None)))


def _sum(session: Session, household_id: int, start: date, end: date,
         positive: bool, category_id: int | None = None,
         internal: bool | None = None,
         account_ids: list[int] | None = None) -> int:
    wheres = [Account.household_id == household_id,
              Transaction.status != TransactionStatus.void,
              Transaction.date >= start, Transaction.date <= end,
              *([Account.id.in_(account_ids)] if account_ids is not None else []),
              ~((Transaction.source == "system") & Transaction.external_ref.like("open:%"))
              | Transaction.external_ref.is_(None)]
    helper = (internal_category_amounts if internal is True else
              external_category_amounts if internal is False else splits.effective_category_amounts)
    amounts = helper(*wheres)
    side = (income_leg(amounts) if positive else expense_leg(amounts))
    if internal is True:
        side = amounts.c.amount_pence > 0 if positive else amounts.c.amount_pence < 0
    stmt = select(func.coalesce(func.sum(amounts.c.amount_pence), 0)).where(side)
    if category_id is not None:
        stmt = stmt.where(amounts.c.category_id == category_id)
    return int(session.execute(stmt).scalar_one())


def _count(session: Session, household_id: int, start: date, end: date,
           internal: bool, account_ids: list[int] | None = None) -> int:
    helper = internal_category_amounts if internal else external_category_amounts
    amounts = helper(
        Account.household_id == household_id,
        Transaction.status != TransactionStatus.void,
        Transaction.date >= start, Transaction.date <= end,
        ~((Transaction.source == "system") & Transaction.external_ref.like("open:%"))
        | Transaction.external_ref.is_(None),
        *([Account.id.in_(account_ids)] if account_ids is not None else []))
    rows = session.execute(select(amounts.c.transfer_id, amounts.c.transaction_id).distinct())
    return len({("pair", transfer_id) if transfer_id is not None else ("row", transaction_id)
                for transfer_id, transaction_id in rows})

def _fig(key: str, label: str, amount: int | None,
         drill: dict[str, Any] | None = None,
         components: list[dict[str, Any]] | None = None,
         count: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"key": key, "label": label,
                           "amount_pence": amount}
    if drill is not None:
        out["drill"] = drill
    if components:
        out["components"] = components
    if count is not None:
        out["count"] = count
    return out


def _sections(session: Session, household_id: int,
              start: date, end: date,
              account_ids: list[int] | None = None,
              budget_ids: list[int] | None = None) -> list[dict[str, Any]]:
    income = _sum(session, household_id, start, end,
                  positive=True, internal=False, account_ids=account_ids)
    expense = -_sum(session, household_id, start, end,
                    positive=False, internal=False, account_ids=account_ids)
    income_drill = _drill(date_from=start, date_to=end,
                          budget_side="income")
    expense_drill = _drill(date_from=start, date_to=end,
                           budget_side="expense")

    ea = external_category_amounts(
        Account.household_id == household_id,
        Transaction.status != TransactionStatus.void,
        Transaction.date >= start, Transaction.date <= end,
        Transaction.transfer_id.is_(None),
        *([Account.id.in_(account_ids)] if account_ids is not None else []),
        ~((Transaction.source == "system") & Transaction.external_ref.like("open:%"))
        | Transaction.external_ref.is_(None))
    cat_rows = session.execute(
        select(ea.c.category_id, func.sum(ea.c.amount_pence))
        .where(expense_leg(ea))
        .group_by(ea.c.category_id)
        .order_by(ea.c.category_id.is_(None), ea.c.category_id)).all()
    names = {c.id: c.name for c in session.execute(
        select(Category).where(Category.household_id == household_id))
        .scalars()}
    cat_figs = [_fig(
        "uncategorised" if cid is None else f"category_{cid}",
        names.get(cid, f"Category {cid}") if cid is not None
        else "Uncategorised",
        -total,
        _drill(date_from=start, date_to=end, budget_side="expense",
               uncategorised=True if cid is None else None,
               category_id=cid))
        for cid, total in cat_rows]

    t_total = -_sum(session, household_id, start, end,
                    positive=False, internal=True, account_ids=account_ids)
    t_count = _count(session, household_id, start, end, internal=True,
                     account_ids=account_ids)
    t_drill = _drill(date_from=start, date_to=end,
                     direction="expense", internal="only")

    variance_figs: list[dict[str, Any]] = []
    budgets = session.execute(
        select(Budget)
        .where(Budget.household_id == household_id,
               Budget.start_date >= start, Budget.end_date <= end,
               *([Budget.id.in_(budget_ids)] if budget_ids is not None else []))
        .order_by(Budget.id)).scalars()
    for b in budgets:
        actuals = budget_domain.actuals_for_budget(session, b, account_ids)
        for ln in sorted(b.lines, key=lambda line: line.id):
            actual = actuals[ln.id]
            variance_figs.append(_fig(
                f"budget_{b.id}_line_{ln.id}",
                f"{b.name} · {names.get(ln.category_id, ln.category_id)}",
                ln.effective_pence - actual,
                _drill(date_from=b.start_date, date_to=b.end_date,
                       owner_profile_id=b.owner_profile_id,
                       shared_only=True if b.owner_profile_id is None else None,
                       budget_side=ln.side.value,
                       category_id=ln.category_id)))

    savings = income - expense
    rate = (round(savings * 10000 / income)
            if income > 0 else None)
    return [
        {"key": "income", "label": "Income", "figures": [_fig(
            "income_total", "Total income", income, income_drill)]},
        {"key": "spend_by_category", "label": "Spend by category",
         "figures": cat_figs},
        {"key": "transfers", "label": "Internal transfers",
         "figures": [
             _fig("transfer_total", "Transferred between accounts",
                  t_total, t_drill),
             _fig("transfer_count", "Internal movements", None,
                  _drill(date_from=start, date_to=end, internal="only"), count=t_count)]},
        {"key": "savings", "label": "Savings", "figures": [
            _fig("savings_pence", "Net savings", savings,
                 components=[income_drill, expense_drill]),
            _fig("savings_rate_bps", "Savings rate (bps)", rate,
                 components=[income_drill, expense_drill])]},
        {"key": "variances", "label": "Budget variances",
         "figures": variance_figs},
    ]


def build_report(session: Session, household_id: int,
                 start: date, end: date,
                 prior: tuple[date, date] | None = None,
                 account_ids: list[int] | None = None,
                 budget_ids: list[int] | None = None
                 ) -> dict[str, Any]:
    """Full report document for ``[start, end]``. When ``prior`` is a
    (start, end) window, each figure gains ``prior_amount_pence`` /
    ``delta_pence`` — keys are the union of both periods' figures."""
    sections = _sections(session, household_id, start, end, account_ids, budget_ids)
    doc: dict[str, Any] = {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "sections": sections}
    if prior is None:
        return doc

    prior_secs = _sections(session, household_id, prior[0], prior[1], account_ids, budget_ids)
    prior_idx = {f["key"]: f["amount_pence"]
                 for s in prior_secs for f in s["figures"]}
    seen: set[str] = set()
    for s in sections:
        for f in s["figures"]:
            seen.add(f["key"])
            p = prior_idx.get(f["key"])
            f["prior_amount_pence"] = p
            f["delta_pence"] = (None if f["amount_pence"] is None
                                or p is None
                                else f["amount_pence"] - p)
    for s, ps in zip(sections, prior_secs, strict=True):
        for pf in ps["figures"]:
            if pf["key"] not in seen:
                s["figures"].append({
                    "key": pf["key"], "label": pf["label"],
                    "amount_pence": None,
                    "prior_amount_pence": pf["amount_pence"],
                    "delta_pence": (None
                                    if pf["amount_pence"] is None
                                    else -pf["amount_pence"])})
    doc["prior_period"] = {"start": prior[0].isoformat(),
                           "end": prior[1].isoformat()}
    return doc
