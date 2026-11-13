"""Authorized opening ledger stock and received funding, separate from plans."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.domain.reporting import (
    external_category_amounts,
    income_leg,
    internal_category_amounts,
)
from app.persistence.models import (
    Account,
    Budget,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Transaction,
    TransactionStatus,
    TransferRule,
)


def _opening_funds(session: Session, account_ids: list[int], start: date) -> int:
    """Stock includes every posted ledger leg and opening, never account metadata."""
    before = min(start, date.today() + timedelta(days=1))
    return int(session.scalar(select(func.coalesce(func.sum(Transaction.amount_pence), 0))
        .where(Transaction.account_id.in_(account_ids), Transaction.date < before,
               Transaction.status != TransactionStatus.void)) or 0)


def budget_funding(
    session: Session, budget: Budget, account_ids: list[int], *, start: date,
    end: date, include_private: bool = False,
) -> dict[str, Any]:
    """Account ids are the authorized union; budget ownership narrows its scope.

    Counterparty ownership verifies a boundary but only authorized accounts
    may supply attribution. No counterparty account names or ids are returned.
    """
    authorized = {a.id: a for a in session.scalars(select(Account).where(
        Account.household_id == budget.household_id, Account.id.in_(account_ids)))}
    scoped = [a.id for a in authorized.values()
              if (include_private and budget.owner_profile_id is None)
              or a.owner_profile_id == budget.owner_profile_id]
    shared_view = budget.owner_profile_id is None and not include_private
    through = min(end, date.today())
    wheres = (
        Account.id.in_(scoped), Transaction.date >= start, Transaction.date <= through,
        Transaction.status != TransactionStatus.void,
        ~((Transaction.source == "system") & Transaction.external_ref.like("open:%"))
        | Transaction.external_ref.is_(None),
    )
    external = external_category_amounts(*wheres, classify_confirmed=False)
    internal = internal_category_amounts(*wheres)

    def items(amounts: Any, *, receipts: bool = False) -> list[dict[str, Any]]:
        stmt = select(amounts, Transaction.account_id, Transaction.payee).join(
            Transaction, Transaction.id == amounts.c.transaction_id)
        if receipts:
            stmt = stmt.where(income_leg(amounts))
        return [{"transaction_id": r.transaction_id, "date": r.date,
                 "account_id": r.account_id, "payee": r.payee,
                 "category_id": r.category_id, "amount_pence": r.amount_pence,
                 "contributor_profile_id": None}
                for r in session.execute(stmt.order_by(amounts.c.date,
                                                       amounts.c.transaction_id,
                                                       amounts.c.category_id))]

    income = items(external, receipts=True)
    internal_items = items(internal)
    rows = {t.id: t for t in session.scalars(select(Transaction).where(
        Transaction.account_id.in_(scoped), Transaction.date >= start,
        Transaction.date <= through, Transaction.status != TransactionStatus.void))}
    transfer_ids = {t.transfer_id for t in rows.values() if t.transfer_id is not None}
    legs: dict[int, list[tuple[Transaction, Account]]] = {}
    for tx, account in session.execute(select(Transaction, Account).join(Account).where(
            Account.household_id == budget.household_id,
            Transaction.transfer_id.in_(transfer_ids),
            Transaction.status != TransactionStatus.void)):
        assert tx.transfer_id is not None
        legs.setdefault(tx.transfer_id, []).append((tx, account))

    # Known paired boundaries consume all effective internal legs. A shared-space
    # transfer is known internal movement, and therefore never unknown funding.
    boundaries: dict[int, tuple[bool, int | None]] = {}
    for tx in rows.values():
        pair = legs.get(tx.transfer_id, []) if tx.transfer_id is not None else []
        if len(pair) != 2 or sum(t.amount_pence for t, _ in pair) != 0:
            continue
        counterparts = [(t, a) for t, a in pair
                        if t.id != tx.id and t.account_id != tx.account_id
                        and t.amount_pence * tx.amount_pence < 0]
        if len(counterparts) != 1:
            continue
        _, other = counterparts[0]
        own = authorized[tx.account_id]
        boundary = own.owner_profile_id is None and other.owner_profile_id is not None
        profile = other.owner_profile_id if other.id in authorized else None
        boundaries[tx.id] = (boundary, profile)

    # A manual expected-match is explicit evidence even for an unpaired bank leg.
    # Partial matches consume same-direction internal legs first, then external
    # legs. Opposing legs retain their economic classification and amount.
    confirmed: dict[int, tuple[int, int | None, bool]] = {}
    orphaned: set[int] = set()
    source_account, dest_account = aliased(Account), aliased(Account)
    matches = session.execute(select(ExpectedMatch, ExpectedMovement, TransferRule,
        source_account, dest_account).join(
            ExpectedMovement, ExpectedMatch.movement_id == ExpectedMovement.id).join(
            TransferRule, ExpectedMovement.source_id == TransferRule.id, isouter=True).outerjoin(
            source_account, func.coalesce(ExpectedMovement.transfer_source_account_id,
                                         TransferRule.source_account_id) == source_account.id
        ).outerjoin(
            dest_account, func.coalesce(ExpectedMovement.transfer_dest_account_id,
                                       TransferRule.dest_account_id) == dest_account.id).where(
            ExpectedMatch.transaction_id.in_(rows), ExpectedMatch.manual.is_(True),
            ExpectedMovement.source_type == ExpectedSource.transfer_rule,
            ExpectedMovement.household_id == budget.household_id))
    for match, movement, _rule, source, dest in matches:
        tx = rows[match.transaction_id]
        if (tx.transfer_id is not None or movement.account_id != tx.account_id
                or tx.refund_of_id is not None):
            continue
        if (match.matched_pence <= 0
                or tx.amount_pence * movement.amount_pence <= 0):
            continue
        if (_rule is None and movement.transfer_source_account_id is None
                and movement.transfer_dest_account_id is None):
            # Legacy deletion retained the confirmation but no counterparty.
            # Preserve its economics; report the funding as unattributed.
            previous = confirmed.get(tx.id, (0, None, False))[0]
            confirmed[tx.id] = (min(abs(tx.amount_pence), previous + match.matched_pence),
                                None, False)
            orphaned.add(tx.id)
            continue
        if (source is None or dest is None
                or source.household_id != budget.household_id
                or dest.household_id != budget.household_id
                or (_rule is not None and _rule.household_id != budget.household_id)):
            continue
        inbound = (tx.account_id == dest.id and tx.amount_pence > 0
                   and movement.amount_pence > 0)
        outbound = (tx.account_id == source.id and tx.amount_pence < 0
                    and movement.amount_pence < 0)
        if not (inbound or outbound):
            continue
        other = source if inbound else dest
        own = dest if inbound else source
        boundary = own.owner_profile_id is None and other.owner_profile_id is not None
        previous = confirmed.get(tx.id, (0, None, False))[0]
        confirmed[tx.id] = (min(abs(tx.amount_pence), previous + match.matched_pence),
                            other.owner_profile_id if other.id in authorized else None,
                            boundary)

    contributions: list[dict[str, Any]] = []
    unattributed = 0
    for item in internal_items:
        tid, amount = item["transaction_id"], item["amount_pence"]
        if tid in boundaries:
            boundary, profile = boundaries[tid]
            if shared_view and boundary:
                contributions.append({**item, "contributor_profile_id": profile})
            continue
        remaining, profile, boundary = confirmed.get(tid, (0, None, False))
        matched = (min(abs(amount), remaining)
                   if amount * rows[tid].amount_pence > 0 else 0)
        signed = matched if amount > 0 else -matched
        if shared_view and boundary and matched:
            contributions.append({**item, "amount_pence": signed,
                                  "contributor_profile_id": profile})
        if tid in confirmed:
            confirmed[tid] = (remaining - matched, profile, boundary)
        unattributed += amount if tid in orphaned else amount - signed
    for item in income:
        if rows[item["transaction_id"]].amount_pence <= 0:
            continue
        remaining, profile, boundary = confirmed.get(item["transaction_id"], (0, None, False))
        matched = min(item["amount_pence"], remaining)
        if shared_view and boundary and matched:
            contributions.append({**item, "amount_pence": matched,
                                  "contributor_profile_id": profile})
        item["amount_pence"] -= matched
        if item["transaction_id"] in orphaned:
            unattributed += matched
        if item["transaction_id"] in confirmed:
            confirmed[item["transaction_id"]] = (remaining - matched, profile, boundary)

    for item in items(external):
        if (item["amount_pence"] >= 0
                or rows[item["transaction_id"]].amount_pence >= 0):
            continue
        remaining, profile, boundary = confirmed.get(item["transaction_id"], (0, None, False))
        matched = min(-item["amount_pence"], remaining)
        if item["transaction_id"] in orphaned:
            unattributed -= matched
        if shared_view and boundary and matched:
            contributions.append({**item, "amount_pence": -matched,
                                  "contributor_profile_id": profile})
        if item["transaction_id"] in confirmed:
            confirmed[item["transaction_id"]] = (remaining - matched, profile, boundary)

    # Economic receipts come from the same classifier as reports and Budget
    # actuals; the raw-leg pass above is only for contributor attribution.
    received = items(external_category_amounts(*wheres), receipts=True)
    return {"opening_funds_pence": _opening_funds(session, scoped, start),
            "opening_as_of": min(start - timedelta(days=1), date.today()),
            "external_income_pence": sum(i["amount_pence"] for i in received),
            "income_items": received,
            "contributions_pence": sum(i["amount_pence"] for i in contributions),
            "contribution_items": sorted(contributions,
                                         key=lambda i: (i["date"], i["transaction_id"])),
            "unattributed_internal_pence": unattributed}
