"""Transaction splits (issue #289).

A transaction with splits reports as N ``(category, amount)`` rows;
one without behaves as a single implicit split on its own
``category_id`` — so unsplit history needs no migration and every
rollup can share one effective-category subquery.
"""

from __future__ import annotations

from sqlalchemy import exists, select, union_all
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import ColumnElement, Subquery

from app.persistence.models import (
    Account,
    Category,
    Transaction,
    TransactionSplit,
)


class SplitError(ValueError):
    """Raised for invalid split sets (bad sum, empty legs, unknown
    category, immutability violations)."""


def replace(
    session: Session, txn: Transaction, household_id: int,
    items: list[dict[str, int]],
) -> list[TransactionSplit]:
    """Replace ``txn``'s split set. ``items`` are
    ``{"category_id", "amount_pence"}`` dicts; an empty list clears the
    split (transaction reverts to its own ``category_id``).

    Rules (issue #289): splits must sum to the parent's signed amount,
    every leg needs a real household category, and a single-leg split
    is pointless — use the transaction's ``category_id`` instead.
    """
    from app.domain.periods import assert_editable

    assert_editable(session, txn)
    if not items:
        txn.splits.clear()
        session.flush()
        return []
    if len(items) == 1:
        raise SplitError(
            "a single split adds nothing — set the transaction's "
            "category_id instead")
    total = sum(int(i["amount_pence"]) for i in items)
    if total != txn.amount_pence:
        raise SplitError(
            f"split total {total}p does not equal the transaction "
            f"amount {txn.amount_pence}p")
    if txn.amount_pence != 0:
        direction = 1 if txn.amount_pence > 0 else -1
        if any(int(i["amount_pence"]) * direction <= 0 for i in items):
            raise SplitError(
                "every split must share the transaction's sign "
                "(income legs positive, expense legs negative)")
    cat_ids = {int(i["category_id"]) for i in items}
    owned = set(session.scalars(
        select(Category.id).where(Category.household_id == household_id,
                                  Category.id.in_(cat_ids))))
    missing = cat_ids - owned
    if missing:
        raise SplitError(
            f"unknown or foreign categories: {sorted(missing)}")
    txn.splits.clear()
    session.flush()
    rows = [
        TransactionSplit(
            category_id=int(i["category_id"]),
            amount_pence=int(i["amount_pence"]), position=idx)
        for idx, i in enumerate(items)]
    txn.splits.extend(rows)
    session.flush()
    return rows


def copy_for_correction(
    session: Session, source: Transaction, corrected: Transaction,
) -> None:
    """Carry splits onto a correction row (issue #289): the corrected
    amount is unchanged, so the existing split set still sums."""
    for idx, s in enumerate(source.splits):
        session.add(TransactionSplit(
            transaction_id=corrected.id, category_id=s.category_id,
            amount_pence=s.amount_pence, position=idx))


def effective_category_amounts(*wheres: ColumnElement[bool]) -> Subquery:
    """A subquery of ``(category_id, amount_pence)`` rows: one row per
    split leg for split transactions, one row per unsplit transaction.

    ``wheres`` are ``Transaction``/``Account``-column predicates
    (household scope, date range, status, sign, transfer exclusions)
    applied inside both legs — both legs join ``Account`` and the
    split leg joins ``Transaction``, so the same clauses work verbatim.
    """
    unsplit = (
        select(
            Transaction.category_id.label("category_id"),
            Transaction.amount_pence.label("amount_pence"))
        .join(Account, Transaction.account_id == Account.id)
        .where(
            ~exists().where(
                TransactionSplit.transaction_id == Transaction.id),
            *wheres))
    split = (
        select(
            TransactionSplit.category_id.label("category_id"),
            TransactionSplit.amount_pence.label("amount_pence"))
        .join(
            Transaction,
            TransactionSplit.transaction_id == Transaction.id)
        .join(Account, Transaction.account_id == Account.id)
        .where(*wheres))
    return union_all(unsplit, split).subquery()
