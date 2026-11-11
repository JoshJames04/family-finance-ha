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
    Category,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    MatchType,
    ProviderCategoryMap,
    RecurringCommitment,
    RuleSource,
    Subscription,
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


_MATCH_CATEGORY_SOURCES = {
    ExpectedSource.commitment: RecurringCommitment,
    ExpectedSource.subscription: Subscription,
}


def inherit_match_categories(session: Session, household_id: int
                             ) -> int:
    """Copy the matched source's category onto uncategorised
    transactions (issue #452).

    An ExpectedMatch already asserts the transaction *is* that
    commitment/subscription occurrence — inheriting its category is a
    consequence of the match, not a silent guess (which is why this
    sets ``category_id`` where rules may only suggest). Covers both
    manual and scored-auto matches: the reconcile scorer never
    auto-accepts an ambiguous match. Idempotent — it only touches
    ``category_id IS NULL`` rows, so it backfills history on every
    post-sync pass without churning user-set categories."""
    n = 0
    rows = session.execute(
        select(ExpectedMatch, ExpectedMovement, Transaction)
        .join(ExpectedMovement,
              ExpectedMatch.movement_id == ExpectedMovement.id)
        .join(Transaction,
              ExpectedMatch.transaction_id == Transaction.id)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.category_id.is_(None))).all()
    for _match, mv, tx in rows:
        model = _MATCH_CATEGORY_SOURCES.get(mv.source_type)
        if model is None:
            continue
        src = session.get(model, mv.source_id)
        cid = getattr(src, "category_id", None) if src else None
        if cid is not None:
            tx.category_id = cid
            n += 1
            # Teach the association (issue #453): a tx that IS a known
            # bill occurrence earns a payee→category rule so future
            # payments from the same payee at least arrive suggested,
            # even if amount/date drift breaks the reconcile match.
            learn(session, household_id, tx.payee, cid)
    session.flush()
    return n


def apply_provider_map(session: Session, household_id: int) -> int:
    """Provider-enum → household category, user-authored at Settings
    (issue #453). Auto-applies to uncategorised transactions carrying
    ``provider_category`` — an explicit mapping is user intent, not a
    silent guess. Unmapped enums fall back to a name-match suggestion
    ("BILLS_AND_SERVICES" → a category named "Bills and services")
    rather than a real categorisation."""
    cat_by_norm: dict[str, int] = {}
    for cid, name in session.execute(
            select(Category.id, Category.name)
            .where(Category.household_id == household_id)):
        cat_by_norm.setdefault(re.sub(r"[^a-z0-9]+", " ",
                               name.casefold()).strip(), cid)
    mapping = {(m.provider, m.provider_category): m.category_id
               for m in session.scalars(
                   select(ProviderCategoryMap).where(
                       ProviderCategoryMap.household_id == household_id))}
    applied = 0
    rows = session.scalars(
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.category_id.is_(None),
               Transaction.provider_category.isnot(None),
               Transaction.status != TransactionStatus.void))
    for tx in rows:
        pc = tx.provider_category
        if pc is None:
            continue
        mapped = mapping.get((tx.source.value, pc))
        if mapped is not None:
            tx.category_id = mapped
            applied += 1
        elif tx.suggested_category_id is None:
            guess = cat_by_norm.get(re.sub(
                r"[^a-z0-9]+", " ", pc.casefold()).strip())
            if guess is not None:
                tx.suggested_category_id = guess
    session.flush()
    return applied


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
