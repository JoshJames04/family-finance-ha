"""Issue #68 — auto-categorisation rules, transfer/refund matching.

Rules match payee patterns → category, priority-ordered (lower wins),
``times_confirmed`` breaking ties — so every confirmed correction
strengthens the rule that earns future suggestions. A rule with
``auto_apply=False`` only fills ``suggested_category_id``: a
suggestion is a flag, never a silent categorisation.

``auto_pair_transfers`` identifies internal-account movements on a
synthetic feed: same date, equal magnitude, opposite signs, two
different accounts, both legs unlinked → one Transfer object (ADR-04)
which the ``external_only`` contract then excludes from spend/income.

``auto_link_refunds`` ties a positive txn to the earlier same-payee
debit on the same account via ``refunds.link_refund`` (which enforces
the refundable balance).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.refunds import link_refund, refundable_balance
from app.persistence.models import (
    Account,
    CategorisationRule,
    MatchType,
    RuleSource,
    Transaction,
    TransactionStatus,
    Transfer,
)


def _matches(rule: CategorisationRule, payee: str) -> bool:
    if rule.match_type is MatchType.exact:
        return rule.pattern.casefold() == payee.casefold()
    if rule.match_type is MatchType.regex:
        try:
            return re.search(rule.pattern, payee,
                             re.IGNORECASE) is not None
        except re.error:
            return False
    return rule.pattern.casefold() in payee.casefold()


def match_rule(session: Session, household_id: int, payee: str
               ) -> CategorisationRule | None:
    """First enabled matching rule: priority asc, then the most-
    confirmed rule wins a tie — learning earns precedence."""
    rules = session.scalars(
        select(CategorisationRule)
        .where(CategorisationRule.household_id == household_id,
               CategorisationRule.enabled.is_(True))
        .order_by(CategorisationRule.priority,
                  CategorisationRule.times_confirmed.desc(),
                  CategorisationRule.id))
    return next((r for r in rules if _matches(r, payee)), None)


@dataclass(frozen=True)
class ApplyResult:
    auto_applied: int
    suggested: int
    unmatched: int


def apply_rules(session: Session, household_id: int,
                since: date | None = None) -> ApplyResult:
    """Suggest/apply categories on uncategorised transactions."""
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.category_id.is_(None),
               Transaction.status != TransactionStatus.void))
    if since is not None:
        stmt = stmt.where(Transaction.date >= since)
    applied = suggested = unmatched = 0
    for t in session.scalars(stmt):
        rule = match_rule(session, household_id, t.payee)
        if rule is None:
            unmatched += 1
        elif rule.auto_apply:
            t.category_id = rule.category_id
            t.suggested_category_id = None
            applied += 1
        else:
            t.suggested_category_id = rule.category_id
            suggested += 1
    session.flush()
    return ApplyResult(applied, suggested, unmatched)


def learn(session: Session, household_id: int, payee: str,
          category_id: int) -> CategorisationRule:
    """Confirmed categorisation → future suggestions (the AC). An
    existing exact rule for the payee absorbs the correction
    (retarget + strengthen); otherwise a learned rule is minted."""
    rule = session.scalar(
        select(CategorisationRule).where(
            CategorisationRule.household_id == household_id,
            CategorisationRule.pattern == payee,
            CategorisationRule.match_type == MatchType.exact))
    if rule is None:
        rule = CategorisationRule(
            household_id=household_id, pattern=payee,
            match_type=MatchType.exact, category_id=category_id,
            source=RuleSource.learned)
        session.add(rule)
    else:
        rule.category_id = category_id
    rule.times_confirmed = (rule.times_confirmed or 0) + 1
    session.flush()
    return rule


def auto_pair_transfers(session: Session, household_id: int,
                        since: date | None = None) -> int:
    """Pair same-date equal-magnitude opposite-sign legs across
    different accounts into Transfer objects. Returns pairs made."""
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.transfer_id.is_(None),
               Transaction.status != TransactionStatus.void)
        .order_by(Transaction.date, Transaction.id))
    if since is not None:
        stmt = stmt.where(Transaction.date >= since)
    groups: dict[tuple[date, int], list[Transaction]] = {}
    for t in session.scalars(stmt):
        groups.setdefault((t.date, abs(t.amount_pence)), []).append(t)
    pairs = 0
    for (_, _), legs in groups.items():
        debits = [t for t in legs if t.amount_pence < 0]
        credits = [t for t in legs if t.amount_pence > 0]
        for d, c in zip(debits, credits, strict=False):
            if d.account_id == c.account_id:
                continue
            tr = Transfer(household_id=household_id)
            session.add(tr)
            session.flush()
            d.transfer_id = tr.id
            c.transfer_id = tr.id
            pairs += 1
    session.flush()
    return pairs


def auto_link_refunds(session: Session, household_id: int,
                      since: date | None = None) -> int:
    """Link positive txns to the earlier same-payee debit on the same
    account, bounded by the refundable balance."""
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.amount_pence > 0,
               Transaction.refund_of_id.is_(None),
               Transaction.transfer_id.is_(None),
               Transaction.status != TransactionStatus.void)
        .order_by(Transaction.date, Transaction.id))
    if since is not None:
        stmt = stmt.where(Transaction.date >= since)
    linked = 0
    for t in session.scalars(stmt):
        original = session.scalar(
            select(Transaction)
            .where(Transaction.account_id == t.account_id,
                   Transaction.payee == t.payee,
                   Transaction.amount_pence < 0,
                   Transaction.date <= t.date,
                   Transaction.id != t.id,
                   Transaction.status != TransactionStatus.void)
            .order_by(Transaction.date.desc(), Transaction.id.desc()))
        if original is None:
            continue
        if t.amount_pence <= refundable_balance(session, original.id):
            link_refund(session, t, original)
            linked += 1
    session.flush()
    return linked
