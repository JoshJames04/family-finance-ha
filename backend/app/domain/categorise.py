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
    BankAccountMapping,
    CategorisationRule,
    Category,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    MappingStatus,
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


def match_rule(session: Session, household_id: int, payee: str,
               *, account_id: int | None = None, amount_pence: int | None = None
               ) -> CategorisationRule | None:
    """First enabled matching rule: priority asc, then the most-
    specific scope wins a tie, then confirmations earn precedence."""
    rules = session.scalars(
        select(CategorisationRule)
        .where(CategorisationRule.household_id == household_id,
               CategorisationRule.enabled.is_(True))
        .order_by(CategorisationRule.priority,
                  CategorisationRule.account_id.is_(None),
                  CategorisationRule.amount_pence.is_(None),
                  CategorisationRule.times_confirmed.desc(),
                  CategorisationRule.id))
    return next((r for r in rules if _matches(r, payee)
                 and (r.account_id is None or r.account_id == account_id)
                 and (r.amount_pence is None or r.amount_pence == amount_pence)), None)


@dataclass(frozen=True)
class ApplyResult:
    auto_applied: int
    suggested: int
    unmatched: int


def apply_rules(session: Session, household_id: int,
                since: date | None = None, *,
                account_ids: list[int] | None = None) -> ApplyResult:
    """Suggest/apply categories on uncategorised transactions."""
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.category_id.is_(None),
               Transaction.status != TransactionStatus.void))
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    if since is not None:
        stmt = stmt.where(Transaction.date >= since)
    applied = suggested = unmatched = 0
    for t in session.scalars(stmt):
        rule = match_rule(session, household_id, t.payee,
                          account_id=t.account_id, amount_pence=t.amount_pence)
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
            learn(session, household_id, tx.payee, cid, account_id=tx.account_id)
    session.flush()
    return n


def apply_provider_map(session: Session, household_id: int, *,
                       account_ids: list[int] | None = None) -> int:
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
        if account_ids is not None and tx.account_id not in account_ids:
            continue
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
          category_id: int, *, account_id: int | None = None) -> CategorisationRule:
    """Confirmed categorisation → future suggestions (the AC). An
    existing exact rule for the payee absorbs the correction
    (retarget + strengthen); otherwise a learned rule is minted."""
    rule = session.scalar(
        select(CategorisationRule).where(
            CategorisationRule.household_id == household_id,
            CategorisationRule.pattern == payee,
            CategorisationRule.match_type == MatchType.exact,
            CategorisationRule.account_id == account_id,
            CategorisationRule.amount_pence.is_(None),
            CategorisationRule.source == RuleSource.learned))
    if rule is None:
        rule = CategorisationRule(
            household_id=household_id, pattern=payee,
            match_type=MatchType.exact, category_id=category_id,
            source=RuleSource.learned, account_id=account_id)
        session.add(rule)
    else:
        rule.category_id = category_id
    rule.times_confirmed = (rule.times_confirmed or 0) + 1
    session.flush()
    return rule


def _provider_alias_target(session: Session, household_id: int, tx: Transaction) -> int | None:
    """A provider label is owned only within confirmed connection/account identity."""
    if tx.source != "starling":
        return None
    mappings = list(session.scalars(select(BankAccountMapping).join(
        Account, BankAccountMapping.account_id == Account.id).where(
            BankAccountMapping.household_id == household_id,
            Account.household_id == household_id,
            BankAccountMapping.status == MappingStatus.linked)))
    sources = {(m.connection_id, m.external_uid) for m in mappings if m.account_id == tx.account_id}
    matches = {m.account_id for m in mappings
               if (m.connection_id, m.external_uid) in sources
               and m.external_label.strip().casefold() == tx.payee.strip().casefold()}
    return next(iter(matches)) if len(matches) == 1 else None


def _transfer_target(session: Session, household_id: int, tx: Transaction) -> int | None:
    rule = match_rule(session, household_id, tx.payee,
                      account_id=tx.account_id, amount_pence=tx.amount_pence)
    if rule is not None and rule.source == RuleSource.manual and rule.auto_apply:
        return rule.transfer_account_id
    return _provider_alias_target(session, household_id, tx)


def classify_owned_movements(session: Session, household_id: int) -> int:
    """Known owned-account counterparties are internal movements.

    Preserve authored categories and counterparty text. Name matches must be
    unique and refer to another household account; no fuzzy person matching.
    """
    accounts = list(session.scalars(select(Account).where(
        Account.household_id == household_id)))
    names: dict[str, list[int]] = {}
    for account in accounts:
        names.setdefault(account.name.strip().casefold(), []).append(account.id)
    category_id = session.scalar(select(Category.id).where(
        Category.household_id == household_id,
        Category.name == "Internal Transfers"))
    if category_id is None:
        return 0
    count = 0
    for tx in session.scalars(select(Transaction).join(Account).where(
            Account.household_id == household_id,
            Transaction.category_id.is_(None),
            Transaction.status != TransactionStatus.void)):
        destinations = names.get(tx.payee.strip().casefold(), [])
        target = _transfer_target(session, household_id, tx)
        if target is not None:
            destinations = [target]
        if tx.transfer_id is not None or (
                len(destinations) == 1 and destinations[0] != tx.account_id):
            tx.category_id = category_id
            tx.suggested_category_id = None
            count += 1
    session.flush()
    return count


def auto_pair_transfers(session: Session, household_id: int,
                        since: date | None = None, *,
                        account_ids: list[int] | None = None) -> int:
    """Pair only unique reciprocal owned-account identities/aliases,
    same-date opposite amounts and equal currencies. Returns pairs made."""
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.transfer_id.is_(None),
               Transaction.status != TransactionStatus.void)
        .order_by(Transaction.date, Transaction.id))
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    if since is not None:
        stmt = stmt.where(Transaction.date >= since)
    groups: dict[tuple[date, int], list[Transaction]] = {}
    for t in session.scalars(stmt):
        groups.setdefault((t.date, abs(t.amount_pence)), []).append(t)
    accounts = {a.id: a for a in session.scalars(
        select(Account).where(Account.household_id == household_id))}
    pairs = 0
    for (_, _), legs in groups.items():
        debits = [t for t in legs if t.amount_pence < 0]
        credits = [t for t in legs if t.amount_pence > 0]
        def supported(d: Transaction, c: Transaction) -> bool:
            if (d.account_id == c.account_id
                    or accounts[d.account_id].currency != accounts[c.account_id].currency):
                return False
            reciprocal = (
                d.payee.strip().casefold() == accounts[c.account_id].name.strip().casefold()
                and c.payee.strip().casefold() == accounts[d.account_id].name.strip().casefold())
            if reciprocal:
                return True
            aliases = (_transfer_target(session, household_id, d) == c.account_id
                       and _transfer_target(session, household_id, c) == d.account_id)
            return aliases

        for d in debits:
            candidates = [c for c in credits if supported(d, c)]
            if len(candidates) != 1:
                continue
            c = candidates[0]
            if sum(supported(other, c) for other in debits) != 1:
                continue
            tr = Transfer(household_id=household_id)
            session.add(tr)
            session.flush()
            d.transfer_id = tr.id
            c.transfer_id = tr.id
            session.flush()
            from app.domain import goals
            goals.emit_contribution_milestones(session, c)
            pairs += 1
    session.flush()
    return pairs


def auto_link_refunds(session: Session, household_id: int,
                      since: date | None = None, *,
                      account_ids: list[int] | None = None) -> int:
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
    if account_ids is not None:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    if since is not None:
        stmt = stmt.where(Transaction.date >= since)
    linked = 0
    for t in session.scalars(stmt):
        category = session.get(Category, t.category_id) if t.category_id else None
        if category is not None and category.name == "Internal Transfers":
            continue
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
