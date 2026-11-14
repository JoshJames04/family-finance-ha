"""Equivalent refund corrections consume the purchase's refundable balance once."""
from datetime import date, timedelta

import pytest
from sqlalchemy import event

from app.domain import categorise, refunds, transactions
from app.persistence.models import Account, Category, Transaction, TransactionStatus, Transfer

DAY = date(2025, 4, 10)


@pytest.fixture()
def purchase_and_refund(session, household):
    account = Account(household_id=household.id, name="Synthetic", type="joint",
                       opening_date=DAY)
    category = Category(household_id=household.id, name="Synthetic purchase")
    session.add_all([account, category])
    session.flush()
    purchase = Transaction(account_id=account.id, date=DAY, amount_pence=-1000,
        payee="Synthetic", source="manual", category_id=category.id)
    refund = Transaction(account_id=account.id, date=DAY, amount_pence=400,
        payee="Synthetic", source="manual", category_id=category.id)
    session.add_all([purchase, refund])
    session.flush()
    refunds.link_refund(session, refund, purchase)
    return purchase, refund, category


@pytest.mark.parametrize("correction_count", [1, 2, 3])
def test_corrected_refund_counts_only_live_replacement(
        session, purchase_and_refund, correction_count):
    purchase, refund, category = purchase_and_refund
    predecessors = []
    for _ in range(correction_count):
        refund.status = TransactionStatus.reconciled
        predecessors.append(refund)
        refund = transactions.correct(session, refund, "Synthetic metadata correction")
        assert refund.refund_of_id == purchase.id
        assert refunds.refundable_balance(session, purchase.id) == 600
    session.expire_all()
    assert refunds.refundable_balance(session, purchase.id) == 600
    assert all(row.status == TransactionStatus.void for row in predecessors)
    assert all(row.refund_of_id == purchase.id for row in predecessors)
    assert refunds.category_spend(session, category.id, DAY, DAY) == -600
    assert refunds.category_spend(session, category.id, DAY, DAY, net=False) == -1000
    assert not session.new and not session.dirty and not session.deleted


def test_void_final_refund_restores_available_amount(session, purchase_and_refund):
    purchase, refund, category = purchase_and_refund
    refund.status = TransactionStatus.reconciled
    replacement = transactions.correct(session, refund, "Synthetic")
    replacement.status = TransactionStatus.void
    session.flush()
    assert refunds.refundable_balance(session, purchase.id) == 1000
    assert refunds.category_spend(session, category.id, DAY, DAY) == -1000
    assert refund.refund_of_id == replacement.refund_of_id == purchase.id


def test_next_refund_uses_exact_live_remaining_balance(session, purchase_and_refund):
    purchase, refund, _ = purchase_and_refund
    refund.status = TransactionStatus.reconciled
    transactions.correct(session, refund, "Synthetic")
    too_much = Transaction(account_id=purchase.account_id, date=DAY, amount_pence=601,
                            payee="Synthetic excess", source="manual")
    remainder = Transaction(account_id=purchase.account_id, date=DAY, amount_pence=600,
                             payee="Synthetic remainder", source="manual")
    session.add_all([too_much, remainder])
    session.flush()
    with pytest.raises(refunds.RefundError, match="exceeds"):
        refunds.link_refund(session, too_much, purchase)
    assert too_much.refund_of_id is None
    refunds.link_refund(session, remainder, purchase)
    assert remainder.refund_of_id == purchase.id
    assert refunds.refundable_balance(session, purchase.id) == 0
    extra = Transaction(account_id=purchase.account_id, date=DAY, amount_pence=1,
                        payee="Synthetic extra", source="manual")
    session.add(extra)
    session.flush()
    with pytest.raises(refunds.RefundError, match="exceeds"):
        refunds.link_refund(session, extra, purchase)
    assert extra.refund_of_id is None


@pytest.mark.parametrize("count", [1, 2, 3])
def test_legacy_unlinked_refund_replacement_still_consumes_capacity_read_only(
        session, purchase_and_refund, count):
    purchase, refund, _ = purchase_and_refund
    ancestor = refund
    for _ in range(count):
        refund.status = TransactionStatus.reconciled
        refund = transactions.correct(session, refund, "Synthetic legacy correction")
        refund.refund_of_id = None  # pre-upgrade correction omitted this field
        session.flush()
    session.expire_all()
    writes = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        assert refunds.refundable_balance(session, purchase.id) == 600
        assert ancestor.refund_of_id == purchase.id and refund.refund_of_id is None
        assert writes == [] and not session.new and not session.dirty and not session.deleted
    finally:
        event.remove(engine, "before_cursor_execute", observe)


def test_new_linked_descendant_after_legacy_gap_counts_once(session, purchase_and_refund):
    purchase, refund, _ = purchase_and_refund
    refund.status = TransactionStatus.reconciled
    legacy = transactions.correct(session, refund, "Synthetic legacy")
    legacy.refund_of_id = None
    legacy.status = TransactionStatus.reconciled
    current = transactions.correct(session, legacy, "Synthetic new correction")
    current.refund_of_id = purchase.id
    session.flush()
    assert refunds.refundable_balance(session, purchase.id) == 600
    assert not session.new and not session.dirty


def test_legacy_refund_cannot_allow_an_additional_overrefund(session, purchase_and_refund):
    purchase, refund, _ = purchase_and_refund
    refund.status = TransactionStatus.reconciled
    current = transactions.correct(session, refund, "Synthetic legacy")
    current.refund_of_id = None
    too_much = Transaction(account_id=purchase.account_id, date=DAY, amount_pence=601,
                           payee="Synthetic excess", source="manual")
    remainder = Transaction(account_id=purchase.account_id, date=DAY, amount_pence=600,
                            payee="Synthetic remainder", source="manual")
    session.add_all([too_much, remainder])
    session.flush()
    with pytest.raises(refunds.RefundError, match="exceeds"):
        refunds.link_refund(session, too_much, purchase)
    assert too_much.refund_of_id is None
    refunds.link_refund(session, remainder, purchase)
    assert refunds.refundable_balance(session, purchase.id) == 0
    assert current.refund_of_id is None


def test_void_legacy_replacement_restores_available_capacity(session, purchase_and_refund):
    purchase, refund, _ = purchase_and_refund
    refund.status = TransactionStatus.reconciled
    current = transactions.correct(session, refund, "Synthetic legacy")
    current.refund_of_id = None
    current.status = TransactionStatus.void
    session.flush()
    assert refunds.refundable_balance(session, purchase.id) == 1000


@pytest.mark.parametrize("invalid", ["self_cycle", "live_cycle", "void_cycle", "ambiguous",
                                     "amount", "date", "account", "transfer"])
def test_invalid_refund_family_cannot_free_capacity(
        session, household, purchase_and_refund, invalid):
    purchase, refund, _ = purchase_and_refund
    refund.status = TransactionStatus.reconciled
    current = transactions.correct(session, refund, "Synthetic legacy")
    current.refund_of_id = None
    if invalid == "self_cycle":
        refund.corrects_id = refund.id
    elif invalid in {"live_cycle", "void_cycle"}:
        refund.corrects_id = current.id
        if invalid == "void_cycle":
            current.status = TransactionStatus.void
    elif invalid == "ambiguous":
        session.add(Transaction(account_id=current.account_id, date=DAY, amount_pence=400,
            payee="Synthetic other live branch", source="manual", corrects_id=refund.id))
    elif invalid == "amount":
        current.amount_pence = 500
    elif invalid == "date":
        current.date = DAY + timedelta(days=1)
    elif invalid == "account":
        other = Account(household_id=household.id, name="Synthetic other", type="joint",
                        opening_date=DAY)
        session.add(other)
        session.flush()
        current.account_id = other.id
    elif invalid == "transfer":
        transfer = Transfer(household_id=household.id)
        session.add(transfer)
        session.flush()
        current.transfer_id = transfer.id
    session.flush()
    with pytest.raises(refunds.RefundError):
        refunds.refundable_balance(session, purchase.id)
    assert not session.new and not session.dirty and not session.deleted


@pytest.mark.parametrize("change", ["amount", "date", "account"])
def test_explicit_live_link_remains_authoritative_after_edit(
        session, household, purchase_and_refund, change):
    purchase, refund, _ = purchase_and_refund
    refund.status = TransactionStatus.reconciled
    current = transactions.correct(session, refund, "Synthetic explicit correction")
    if change == "amount":
        current.amount_pence = 500
    elif change == "date":
        current.date += timedelta(days=1)
    else:
        other = Account(household_id=household.id, name="Synthetic other", type="joint",
                        opening_date=DAY)
        session.add(other)
        session.flush()
        current.account_id = other.id
    session.flush()
    assert refunds.refundable_balance(session, purchase.id) == (500 if change == "amount" else 600)


def test_explicit_relink_does_not_infer_the_previous_purchase(session, purchase_and_refund):
    purchase, refund, _ = purchase_and_refund
    refund.status = TransactionStatus.reconciled
    current = transactions.correct(session, refund, "Synthetic relink")
    other = Transaction(account_id=purchase.account_id, date=DAY, amount_pence=-1000,
                        payee="Synthetic other purchase", source="manual")
    session.add(other)
    session.flush()
    current.refund_of_id = other.id
    session.flush()
    assert refunds.refundable_balance(session, purchase.id) == 1000
    assert refunds.refundable_balance(session, other.id) == 600


def test_auto_refund_link_skips_invalid_family_and_continues(
        session, household, purchase_and_refund):
    purchase, refund, _ = purchase_and_refund
    refund.status = TransactionStatus.reconciled
    current = transactions.correct(session, refund, "Synthetic legacy")
    current.refund_of_id = None
    sibling = Transaction(account_id=refund.account_id, date=DAY, amount_pence=400,
        payee="Synthetic", source="manual", corrects_id=refund.id)
    other = Transaction(account_id=purchase.account_id, date=DAY, amount_pence=-500,
                         payee="Other", source="manual")
    valid = Transaction(account_id=purchase.account_id, date=DAY, amount_pence=500,
                         payee="Other", source="manual")
    session.add_all([sibling, other, valid])
    session.flush()
    assert categorise.auto_link_refunds(session, household.id) == 1
    assert current.refund_of_id is sibling.refund_of_id is None
    assert valid.refund_of_id == other.id
