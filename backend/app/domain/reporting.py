"""Reporting contract: internal movement is never income or expense.

THE DOUBLE-COUNTING GUARD (issue #90). Any aggregate that sums
``Transaction.amount_pence`` to produce an income/expense/spend figure
MUST exclude internal movement — a transfer between the household's
own accounts moves money internally and changes nothing the household
actually earned or spent.

Budget, report, monthly flow and spending forecast aggregates use
``external_category_amounts``: linked transfers are excluded, and unpaired
effective legs explicitly categorised ``Internal Transfers`` are excluded.
Manually confirmed transfer-rule matches also remove their verified portion,
consuming same-direction internal legs first to retain external fees/receipts.
Each split leg is classified separately so an external fee remains spending.
The ordinary ``Transfer`` category and suggested categories are external by
themselves. A valid manual transfer-rule confirmation additionally identifies
its internal portion, consuming same-direction internal legs first and retaining
external fees/receipts. All economic aggregates share that classification.
``income_leg`` excludes explicit refunds from receipts; ``expense_leg`` keeps
outflows plus explicit refund credits, so their signed total nets only refunds.
``external_only`` is the legacy paired-transfer guard; it cannot replace the
effective-leg helper when an aggregate must separate mixed internal/fee splits.

Deliberate exceptions (each documented where it lives):

- ``accounts.current_balance`` — legs DO move an account's balance;
  excluding them would corrupt the ledger view.
- ``discovery._reconcile_opening`` — computes the opening so that
  ``current_balance`` equals the provider balance; it must sum on
  the same all-legs basis or the reconciled opening is wrong.
- ``transfers.legs_balance`` — deliberately sums a transfer's own legs
  to prove they net to zero.
- ``analytics._monthly_net`` — balance accumulation for materialised
  net worth; legs dated either side of a month boundary are in-flight
  money still owned by the household (same basis as
  ``net_worth_at``/``current_balance``).
- ``analytics.net_worth_at`` — owned balance stock includes every ledger leg.
- ``budget_funding._opening_funds`` — opening stock includes all prior ledger legs.

``tests/test_double_counting.py`` statically enforces the aggregate guard:
every ``func.sum(Transaction.amount_pence)`` site must either call
``external_only`` or be named in the allowlist.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.sql.expression import ColumnElement, Subquery

from app.domain import splits
from app.domain.confirmed_transfers import external_after_confirmed
from app.persistence.models import Category, Transaction, TransactionStatus


def internal_category_ids() -> Select[Any]:
    """Only confirmed internal classification, never suggestions or 'Transfer'."""
    return select(Category.id).where(Category.name == "Internal Transfers")


def external_category_amounts(
    *wheres: ColumnElement[bool], classify_confirmed: bool = True,
) -> Subquery:
    """External effective legs, retaining fees split from an internal movement.

    Funding attribution can request raw external legs and consume confirmed
    matches itself; economic reporting uses the classified default.
    """
    amounts = splits.effective_category_amounts(
        *wheres, Transaction.transfer_id.is_(None),
        Transaction.status != TransactionStatus.void,
        ~((Transaction.source == "system") & Transaction.external_ref.like("open:%"))
        | Transaction.external_ref.is_(None))
    if classify_confirmed:
        return external_after_confirmed(amounts, internal_category_ids())
    return select(amounts).where(or_(
        amounts.c.category_id.is_(None),
        amounts.c.category_id.not_in(internal_category_ids()))).subquery()


def internal_category_amounts(*wheres: ColumnElement[bool]) -> Subquery:
    """Paired legs and confirmed unpaired internal legs, excluding external fees."""
    amounts = splits.effective_category_amounts(*wheres)
    return select(amounts).where(or_(
        amounts.c.transfer_id.is_not(None),
        amounts.c.category_id.in_(internal_category_ids()))).subquery()


def income_leg(amounts: Subquery) -> ColumnElement[bool]:
    """A positive external receipt, excluding explicitly linked refunds."""
    return (amounts.c.amount_pence > 0) & amounts.c.refund_of_id.is_(None)


def expense_leg(amounts: Subquery) -> ColumnElement[bool]:
    """Outflows and positive explicit refunds, whose signed sum is net expense."""
    return or_(amounts.c.amount_pence < 0,
               (amounts.c.amount_pence > 0) & amounts.c.refund_of_id.is_not(None))


def external_only(stmt: Select[Any]) -> Select[Any]:
    """Exclude internal transfer legs from an aggregate query.

    A transfer creates two linked ``Transaction`` rows sharing a
    ``transfer_id``; both are internal movement. Everything without a
    ``transfer_id`` passes this legacy guard. Use ``external_category_amounts``
    for split-aware economic sums including confirmed unpaired movements.
    """
    return stmt.where(Transaction.transfer_id.is_(None))
