"""Automatic passes preserve authored splits, reconciliation and period locks."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.domain import categorise, reporting, settle, splits
from app.persistence.models import (
    Account,
    CategorisationRule,
    Category,
    Confidence,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    RecurringCommitment,
    ReportingPeriod,
    Transaction,
    TransactionSource,
    TransactionStatus,
)


@pytest.mark.parametrize(
    "operation", ["rules", "suggest", "provider", "owned", "pair", "refund", "inherit"]
)
@pytest.mark.parametrize("protection", ["reconciled", "locked_pending", "split"])
def test_automatic_pass_skips_protected_row_and_continues(
    session, household, operation, protection
):
    first = Account(
        household_id=household.id,
        name="Current",
        type="joint",
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    second = Account(
        household_id=household.id,
        name="Bills",
        type="joint",
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    food = Category(household_id=household.id, name="Food")
    internal = Category(household_id=household.id, name="Internal Transfers")
    session.add_all([first, second, food, internal])
    session.flush()
    protected = Transaction(
        account_id=first.id,
        date=date(2025, 4, 10),
        amount_pence=100 if operation == "refund" else -100,
        payee=second.name if operation in {"owned", "pair"} else "Merchant",
        status=TransactionStatus.reconciled
        if protection == "reconciled"
        else TransactionStatus.pending,
        source=TransactionSource.starling,
        suggested_category_id=internal.id,
        provider_category="GENERAL",
    )
    editable = Transaction(
        account_id=first.id,
        date=date(2025, 4, 11),
        amount_pence=100 if operation == "refund" else -200,
        payee=protected.payee,
        status=TransactionStatus.cleared,
        source=TransactionSource.starling,
        provider_category="GENERAL",
    )
    session.add_all([protected, editable])
    session.flush()
    if protection == "locked_pending":
        session.add(
            ReportingPeriod(
                household_id=household.id,
                label="Locked day",
                start_date=protected.date,
                end_date=protected.date,
                locked=True,
            )
        )
    if operation in {"rules", "suggest"}:
        session.add(
            CategorisationRule(
                household_id=household.id,
                pattern="Merchant",
                category_id=food.id,
                auto_apply=operation == "rules",
            )
        )
    elif operation == "provider":
        from app.persistence.models import ProviderCategoryMap

        session.add(
            ProviderCategoryMap(
                household_id=household.id,
                provider="starling",
                provider_category="GENERAL",
                category_id=food.id,
            )
        )
    elif operation == "pair":
        session.add_all(
            [
                Transaction(
                    account_id=second.id, date=protected.date, amount_pence=100, payee=first.name
                ),
                Transaction(
                    account_id=second.id, date=editable.date, amount_pence=200, payee=first.name
                ),
            ]
        )
    elif operation == "refund":
        original = Transaction(
            account_id=first.id,
            date=date(2025, 4, 1),
            amount_pence=-1000,
            payee="Merchant",
            status=TransactionStatus.reconciled,
        )
        session.add(original)
    elif operation == "inherit":
        commitment = RecurringCommitment(
            household_id=household.id,
            name="Merchant",
            account_id=first.id,
            amount_pence=-100,
            frequency=Frequency.monthly,
            next_date=protected.date,
            category_id=food.id,
        )
        session.add(commitment)
        session.flush()
        for row in (protected, editable):
            movement = ExpectedMovement(
                household_id=household.id,
                account_id=first.id,
                expected_date=row.date,
                amount_pence=row.amount_pence,
                label="Merchant",
                confidence=Confidence.scheduled,
                source_type=ExpectedSource.commitment,
                source_id=commitment.id,
            )
            session.add(movement)
            session.flush()
            session.add(
                ExpectedMatch(
                    movement_id=movement.id,
                    transaction_id=row.id,
                    matched_pence=abs(row.amount_pence),
                    manual=False,
                )
            )
    session.flush()
    if protection == "split":
        sign = 1 if protected.amount_pence > 0 else -1
        splits.replace(session, protected, household.id, [
            {"category_id": food.id, "amount_pence": sign * 40},
            {"category_id": internal.id, "amount_pence": sign * 60},
        ])
    before = (
        protected.category_id,
        protected.suggested_category_id,
        protected.transfer_id,
        protected.refund_of_id,
        protected.status,
        protected.amount_pence,
        protected.payee,
    )
    if operation in {"rules", "suggest"}:
        categorise.apply_rules(session, household.id)
    elif operation == "provider":
        categorise.apply_provider_map(session, household.id)
    elif operation == "owned":
        categorise.classify_owned_movements(session, household.id)
    elif operation == "pair":
        categorise.auto_pair_transfers(session, household.id)
    elif operation == "refund":
        categorise.auto_link_refunds(session, household.id)
    else:
        categorise.inherit_match_categories(session, household.id)
    assert (
        protected.category_id,
        protected.suggested_category_id,
        protected.transfer_id,
        protected.refund_of_id,
        protected.status,
        protected.amount_pence,
        protected.payee,
    ) == (
        before[:3] + (original.id,) + before[4:]
        if operation == "refund" and protection == "split"
        else before
    )
    if operation == "refund" and protection == "split":
        assert protected.refund_of_id is not None
    if operation in {"rules", "provider", "inherit"}:
        assert editable.category_id == food.id
    elif operation == "suggest":
        assert editable.suggested_category_id == food.id
    elif operation == "owned":
        assert editable.category_id == internal.id
    elif operation == "pair":
        assert editable.transfer_id is not None
    else:
        assert editable.refund_of_id is not None


@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("authored_split", [False, True])
def test_canonical_match_inherits_before_reconciliation_but_honours_period_lock(
    session, household, locked, authored_split
):
    account = Account(
        household_id=household.id,
        name="Bills",
        type="joint",
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    category = Category(household_id=household.id, name="Subscription")
    session.add_all([account, category])
    session.flush()
    commitment = RecurringCommitment(
        household_id=household.id,
        name="Merchant",
        account_id=account.id,
        amount_pence=-100,
        frequency=Frequency.monthly,
        next_date=date(2025, 4, 10),
        category_id=category.id,
    )
    session.add(commitment)
    session.flush()
    movement = ExpectedMovement(
        household_id=household.id,
        account_id=account.id,
        expected_date=date(2025, 4, 10),
        amount_pence=-100,
        label="Merchant",
        confidence=Confidence.scheduled,
        source_type=ExpectedSource.commitment,
        source_id=commitment.id,
    )
    transaction = Transaction(
        account_id=account.id,
        date=movement.expected_date,
        amount_pence=-100,
        payee="Merchant",
        status=TransactionStatus.pending,
        source=TransactionSource.starling,
    )
    competing = Category(household_id=household.id, name="Competing rule category")
    session.add(competing)
    session.flush()
    session.add(
        CategorisationRule(
            household_id=household.id, pattern="Merchant", category_id=competing.id, auto_apply=True
        )
    )
    session.add_all([movement, transaction])
    session.flush()
    if authored_split:
        splits.replace(session, transaction, household.id, [
            {"category_id": category.id, "amount_pence": -40},
            {"category_id": competing.id, "amount_pence": -60},
        ])
    if locked:
        session.add(
            ReportingPeriod(
                household_id=household.id,
                label="Locked day",
                start_date=movement.expected_date,
                end_date=movement.expected_date,
                locked=True,
            )
        )
    session.flush()
    settle.post_sync(session, household.id, today=movement.expected_date)
    assert transaction.status == (
        TransactionStatus.pending if locked else TransactionStatus.reconciled
    )
    assert transaction.category_id == (None if locked or authored_split else category.id)


@pytest.mark.parametrize("fallback", ["provider", "rule"])
@pytest.mark.parametrize("preexisting", [False, True])
def test_match_without_source_category_applies_only_its_fallback_before_freeze(
    session, household, fallback, preexisting
):
    from app.domain import reconcile
    from app.persistence.models import ProviderCategoryMap

    account = Account(
        household_id=household.id,
        name="Fallback account",
        type="joint",
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    category = Category(household_id=household.id, name="Fallback category")
    authored = Category(household_id=household.id, name="Authored category")
    session.add_all([account, category, authored])
    session.flush()
    commitment = RecurringCommitment(
        household_id=household.id,
        name="Merchant",
        account_id=account.id,
        amount_pence=-100,
        frequency=Frequency.monthly,
        next_date=date(2025, 4, 10),
    )
    session.add(commitment)
    session.flush()
    movement = ExpectedMovement(
        household_id=household.id,
        account_id=account.id,
        expected_date=date(2025, 4, 10),
        amount_pence=-100,
        label="Merchant",
        confidence=Confidence.scheduled,
        source_type=ExpectedSource.commitment,
        source_id=commitment.id,
    )
    transaction = Transaction(
        account_id=account.id,
        date=movement.expected_date,
        amount_pence=-100,
        payee="Merchant",
        status=TransactionStatus.cleared,
        source=TransactionSource.starling,
        provider_category="GENERAL",
        category_id=authored.id if preexisting else None,
    )
    unrelated = Transaction(
        account_id=account.id,
        date=date(2025, 4, 12),
        amount_pence=-777,
        payee="Merchant",
        status=TransactionStatus.cleared,
        source=TransactionSource.starling,
        provider_category="GENERAL",
    )
    session.add_all([movement, transaction, unrelated])
    if fallback == "provider":
        session.add(
            ProviderCategoryMap(
                household_id=household.id,
                provider="starling",
                provider_category="GENERAL",
                category_id=category.id,
            )
        )
    else:
        session.add(
            CategorisationRule(
                household_id=household.id,
                pattern="Merchant",
                category_id=category.id,
                auto_apply=True,
            )
        )
    session.flush()
    reconcile.reconcile_account(session, account, date(2025, 4, 10))
    assert transaction.status == TransactionStatus.reconciled
    assert transaction.category_id == (authored.id if preexisting else category.id)
    assert unrelated.category_id is None


@pytest.mark.parametrize("split_leg", ["debit", "credit", None])
def test_automatic_pairing_preserves_authored_split_reporting(session, household, split_leg):
    accounts = [Account(household_id=household.id, name=name, type="joint",
                        opening_date=date(2025, 1, 1)) for name in ("Current", "Bills")]
    categories = [Category(household_id=household.id, name=name) for name in ("Food", "Fee")]
    session.add_all(accounts + categories)
    session.flush()
    debit = Transaction(account_id=accounts[0].id, date=date(2025, 5, 1),
                        payee=accounts[1].name, amount_pence=-10000, status="cleared")
    credit = Transaction(account_id=accounts[1].id, date=debit.date,
                         payee=accounts[0].name, amount_pence=10000, status="cleared")
    session.add_all([debit, credit])
    session.flush()
    protected = credit if split_leg == "credit" else debit
    if split_leg is not None:
        sign = 1 if split_leg == "credit" else -1
        splits.replace(session, protected, household.id, [
            {"category_id": categories[0].id, "amount_pence": sign * 6000},
            {"category_id": categories[1].id, "amount_pence": sign * 4000},
        ])
    effective = reporting.external_category_amounts(Transaction.id == protected.id)
    before = session.execute(select(effective.c.category_id, effective.c.amount_pence)).all()
    assert sum(amount for _, amount in before) == (10000 if split_leg == "credit" else -10000)
    pairs = categorise.auto_pair_transfers(session, household.id)
    after = session.execute(select(effective.c.category_id, effective.c.amount_pence)).all()
    assert after == ([] if split_leg is None else before)
    assert pairs == (1 if split_leg is None else 0)
    assert (debit.transfer_id is not None) == (split_leg is None)
    assert (credit.transfer_id is not None) == (split_leg is None)
    assert categorise.auto_pair_transfers(session, household.id) == 0
    assert (debit.amount_pence, debit.payee, credit.amount_pence, credit.payee) == (
        -10000, accounts[1].name, 10000, accounts[0].name)


def test_automatic_split_refund_nets_expenses_without_reclassifying_splits(session, household):
    account = Account(household_id=household.id, name="Refund account", type="personal",
                      opening_date=date(2025, 1, 1))
    categories = [Category(household_id=household.id, name=name) for name in ("Food", "Fee")]
    session.add_all([account, *categories])
    session.flush()
    purchase = Transaction(account_id=account.id, date=date(2025, 5, 1), amount_pence=-10000,
                           payee="Merchant", status="cleared")
    refund = Transaction(account_id=account.id, date=date(2025, 5, 2), amount_pence=5000,
                         payee="Merchant", status="cleared")
    session.add_all([purchase, refund])
    session.flush()
    for transaction, amounts in ((purchase, (-6000, -4000)), (refund, (3000, 2000))):
        splits.replace(session, transaction, household.id, [
            {"category_id": category.id, "amount_pence": amount}
            for category, amount in zip(categories, amounts, strict=True)
        ])
    children_before = [(s.id, s.category_id, s.amount_pence) for t in (purchase, refund)
                       for s in t.splits]
    effective = reporting.external_category_amounts(Transaction.account_id == account.id)
    income = select(func.coalesce(func.sum(effective.c.amount_pence), 0)).where(
        reporting.income_leg(effective))
    expense = select(effective.c.category_id, func.sum(effective.c.amount_pence)).where(
        reporting.expense_leg(effective)).group_by(effective.c.category_id).order_by(
            effective.c.category_id)
    assert session.scalar(income) == 5000
    assert session.execute(expense).all() == [(categories[0].id, -6000), (categories[1].id, -4000)]
    linked = categorise.auto_link_refunds(session, household.id)
    assert session.scalar(income) == 0
    assert session.execute(expense).all() == [(categories[0].id, -3000), (categories[1].id, -2000)]
    assert linked == 1 and refund.refund_of_id == purchase.id
    assert [(s.id, s.category_id, s.amount_pence) for t in (purchase, refund)
            for s in t.splits] == children_before
    assert categorise.auto_link_refunds(session, household.id) == 0
