"""Reporting contract: internal movement is never income or expense.

THE DOUBLE-COUNTING GUARD (issue #90). Any aggregate that sums
``Transaction.amount_pence`` to produce an income/expense/spend figure
MUST apply :func:`external_only` — a transfer between the household's
own accounts moves money internally and changes nothing the household
actually earned or spent.

Deliberate exceptions (each documented where it lives):

- ``accounts.current_balance`` — legs DO move an account's balance;
  excluding them would corrupt the ledger view.
- ``discovery._reconcile_opening`` — computes the opening so that
  ``current_balance`` equals the provider balance; it must sum on
  the same all-legs basis or the reconciled opening is wrong.
- ``refunds.refundable_balance`` — scoped by ``refund_of_id``; transfer
  legs never carry that column.
- ``transfers.legs_balance`` — deliberately sums a transfer's own legs
  to prove they net to zero.
- ``analytics._monthly_net`` — balance accumulation for materialised
  net worth; legs dated either side of a month boundary are in-flight
  money still owned by the household (same basis as
  ``net_worth_at``/``current_balance``).

``tests/test_double_counting.py`` statically enforces this contract:
every ``func.sum(Transaction.amount_pence)`` site must either call
``external_only`` or be named in the allowlist.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Select

from app.persistence.models import Transaction


def external_only(stmt: Select[Any]) -> Select[Any]:
    """Exclude internal transfer legs from an aggregate query.

    A transfer creates two linked ``Transaction`` rows sharing a
    ``transfer_id``; both are internal movement. Everything without a
    ``transfer_id`` — purchases, income, refunds — is external and may
    count toward income/expense figures.
    """
    return stmt.where(Transaction.transfer_id.is_(None))
