"""Read-only effective payment evidence, including historical corrections."""
from __future__ import annotations

from collections.abc import Collection
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import Subquery

from app.persistence.models import (
    Account,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Transaction,
    TransactionStatus,
)

SALARY_SOURCES = (ExpectedSource.employment, ExpectedSource.payroll_run)


def _equivalent(left: Any, right: Any) -> ColumnElement[bool]:
    return and_(left.account_id == right.account_id, left.date == right.date,
        left.amount_pence == right.amount_pence,
        left.transfer_id.is_not_distinct_from(right.transfer_id),
        left.refund_of_id.is_not_distinct_from(right.refund_of_id))


def acyclic_transaction_ids(transaction_ids: Collection[int] | Select[Any]) -> Select[Any]:
    """Requested transactions whose directed correction ancestry reaches a root.

    Each transaction has at most one corrects_id parent. A finite parent walk
    reaches a null/missing parent exactly when it contains no directed cycle.
    UNION terminates cyclic walks, but only rooted origins are returned. Status
    and financial equivalence cannot hide a cycle behind a live replacement.
    Only ancestors of the requested transactions are visited; no global scan.
    """
    current, parent, tip = aliased(Transaction), aliased(Transaction), aliased(Transaction)
    ancestry = select(Transaction.id.label("origin_id"),
        Transaction.id.label("transaction_id")).where(
            Transaction.id.in_(transaction_ids)).cte(recursive=True)
    ancestry = ancestry.union(select(ancestry.c.origin_id, parent.id).select_from(ancestry).join(
        current, current.id == ancestry.c.transaction_id).join(
        parent, parent.id == current.corrects_id))
    has_parent = select(parent.id).where(parent.id == tip.corrects_id).correlate(tip).exists()
    return select(ancestry.c.origin_id).join(
        tip, tip.id == ancestry.c.transaction_id).where(~has_parent).distinct()


def eligible_matches(movement_ids: Collection[int] | Select[Any] | None = None, *,
                     transaction_ids: Collection[int] | Select[Any] | None = None) -> Subquery:
    """Resolve each match to one eligible live payment; allocate its pence once.

    Independent directed ancestry rejects cyclic families, including live cycles.
    Only financially equivalent replacements of void predecessors carry evidence;
    UNION deduplicates their correction paths.
    Ambiguous live branches fail closed. Audit rows and their FKs never change.
    Stable match order also caps legacy aliases/duplicate claims to the live
    payment's capacity, while retaining each original partial matched amount.
    """
    parent, child = aliased(Transaction), aliased(Transaction)
    anchor = select(ExpectedMatch.id.label("match_id"),
                    ExpectedMatch.transaction_id.label("transaction_id"))
    if movement_ids is not None or transaction_ids is not None:
        # Traverse only families touched by the caller's requested evidence.
        # Then load ALL claims on those families, so a subset read cannot spend
        # capacity already claimed by another movement through a correction alias.
        if movement_ids is not None:
            seed = select(ExpectedMatch.transaction_id.label("transaction_id")).where(
                ExpectedMatch.movement_id.in_(movement_ids))
        else:
            assert transaction_ids is not None
            seed = select(Transaction.id.label("transaction_id")).where(
                Transaction.id.in_(transaction_ids))
        family = seed.cte(recursive=True)
        family = family.union(select(child.id).select_from(family).join(
            parent, parent.id == family.c.transaction_id).join(child, or_(
                child.corrects_id == parent.id, parent.corrects_id == child.id)).where(
                    _equivalent(parent, child)))
        anchor = anchor.where(ExpectedMatch.transaction_id.in_(select(family.c.transaction_id)))
    requested = anchor.subquery()
    anchor = anchor.where(ExpectedMatch.transaction_id.in_(acyclic_transaction_ids(
        select(requested.c.transaction_id))))
    lineage = anchor.cte(recursive=True)
    lineage = lineage.union(select(lineage.c.match_id, child.id).join(
        parent, parent.id == lineage.c.transaction_id).join(
        child, child.corrects_id == parent.id).where(
        parent.status == TransactionStatus.void,
        _equivalent(parent, child)))
    live = select(lineage.c.match_id, lineage.c.transaction_id,
        func.count().over(partition_by=lineage.c.match_id).label("branches")).join(
        Transaction, Transaction.id == lineage.c.transaction_id).where(
        Transaction.status != TransactionStatus.void).subquery()
    claims = select(ExpectedMatch.id, ExpectedMatch.movement_id,
        live.c.transaction_id, ExpectedMatch.manual,
        func.abs(Transaction.amount_pence).label("capacity"),
        func.min(ExpectedMatch.matched_pence,
                 func.abs(Transaction.amount_pence)).label("claim")).join(
        live, live.c.match_id == ExpectedMatch.id).join(
        Transaction, Transaction.id == live.c.transaction_id).join(
        ExpectedMovement, ExpectedMovement.id == ExpectedMatch.movement_id).join(
        Account, Account.id == Transaction.account_id).where(
        live.c.branches == 1, ExpectedMatch.matched_pence > 0,
        ExpectedMovement.account_id == Transaction.account_id,
        ExpectedMovement.household_id == Account.household_id,
        or_(and_(Transaction.amount_pence > 0, ExpectedMovement.amount_pence > 0),
            and_(Transaction.amount_pence < 0, ExpectedMovement.amount_pence < 0)),
        or_(ExpectedMovement.source_type.not_in(SALARY_SOURCES),
            Transaction.refund_of_id.is_(None))).subquery()
    prior = func.coalesce(func.sum(claims.c.claim).over(
        partition_by=claims.c.transaction_id, order_by=claims.c.id, rows=(None, -1)), 0)
    allocated = select(claims,
        func.min(claims.c.claim, func.max(0, claims.c.capacity - prior)).label(
            "matched_pence")).subquery()
    return select(allocated.c.id, allocated.c.movement_id, allocated.c.transaction_id,
                  allocated.c.manual, allocated.c.matched_pence).where(
        allocated.c.matched_pence > 0).subquery()
