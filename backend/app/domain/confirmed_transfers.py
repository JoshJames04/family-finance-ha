"""Economic legs after explicit confirmation of unpaired internal movements."""
from __future__ import annotations

from typing import Any

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.expression import Subquery

from app.persistence.models import (
    Account,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Transaction,
    TransferRule,
)


def external_after_confirmed(amounts: Subquery, internal_categories: Select[Any]) -> Subquery:
    """Consume confirmed pence, internal legs first, without widening caller scope.

    The input contains every effective leg, including internal categories. Only
    same-direction legs consume evidence; opposing receipts and fees survive.
    Window ROWS handles duplicate category legs without multiplying their pence.
    """
    source, dest, own = aliased(Account), aliased(Account), aliased(Account)
    source_id = func.coalesce(ExpectedMovement.transfer_source_account_id,
                              TransferRule.source_account_id)
    dest_id = func.coalesce(ExpectedMovement.transfer_dest_account_id,
                            TransferRule.dest_account_id)
    # Legacy matched rows deliberately survive rule deletion. With neither
    # endpoint retained, they still prove internal movement, not its attribution.
    orphan = and_(TransferRule.id.is_(None),
                  ExpectedMovement.transfer_source_account_id.is_(None),
                  ExpectedMovement.transfer_dest_account_id.is_(None))
    known = and_(
        or_(TransferRule.id.is_(None), TransferRule.household_id == own.household_id),
        source.household_id == own.household_id, dest.household_id == own.household_id,
        or_(and_(Transaction.account_id == dest.id, Transaction.amount_pence > 0),
            and_(Transaction.account_id == source.id, Transaction.amount_pence < 0)))
    confirmed = (select(
        ExpectedMatch.transaction_id,
        func.min(func.abs(Transaction.amount_pence),
                 func.sum(ExpectedMatch.matched_pence)).label("confirmed_pence"))
        .join(Transaction, Transaction.id == ExpectedMatch.transaction_id)
        .join(own, own.id == Transaction.account_id)
        .join(ExpectedMovement, ExpectedMovement.id == ExpectedMatch.movement_id)
        .outerjoin(TransferRule, TransferRule.id == ExpectedMovement.source_id)
        .outerjoin(source, source.id == source_id)
        .outerjoin(dest, dest.id == dest_id)
        .where(
            ExpectedMatch.manual.is_(True), ExpectedMatch.matched_pence > 0,
            ExpectedMovement.source_type == ExpectedSource.transfer_rule,
            ExpectedMovement.account_id == Transaction.account_id,
            ExpectedMovement.household_id == own.household_id,
            or_(orphan, known),
            Transaction.transfer_id.is_(None), Transaction.refund_of_id.is_(None),
            or_(and_(Transaction.amount_pence > 0,
                     ExpectedMovement.amount_pence > 0),
                and_(Transaction.amount_pence < 0,
                     ExpectedMovement.amount_pence < 0)))
        .group_by(ExpectedMatch.transaction_id, Transaction.amount_pence).subquery())
    internal = case((amounts.c.category_id.in_(internal_categories), 1), else_=0)
    same_direction = amounts.c.amount_pence * Transaction.amount_pence > 0
    legs = (select(
        amounts,
        internal.label("internal"),
        case((same_direction, func.abs(amounts.c.amount_pence)), else_=0).label("eligible"),
        func.coalesce(confirmed.c.confirmed_pence, 0).label("confirmed_pence"))
        .join(Transaction, Transaction.id == amounts.c.transaction_id)
        .outerjoin(confirmed, confirmed.c.transaction_id == amounts.c.transaction_id)
        .subquery())
    prior = func.coalesce(func.sum(legs.c.eligible).over(
        partition_by=legs.c.transaction_id,
        order_by=(legs.c.internal.desc(), legs.c.category_id, legs.c.amount_pence),
        rows=(None, -1)), 0)
    consumed = func.min(legs.c.eligible, func.max(0, legs.c.confirmed_pence - prior))
    adjusted = (select(
        legs.c.transaction_id, legs.c.date, legs.c.transfer_id, legs.c.refund_of_id,
        legs.c.category_id, legs.c.internal,
        (legs.c.amount_pence - case((legs.c.amount_pence > 0, consumed),
                                   else_=-consumed)).label("amount_pence"))
        .subquery())
    return select(
        adjusted.c.transaction_id, adjusted.c.date, adjusted.c.transfer_id,
        adjusted.c.refund_of_id, adjusted.c.category_id, adjusted.c.amount_pence,
    ).where(adjusted.c.internal == 0, adjusted.c.amount_pence != 0).subquery()
