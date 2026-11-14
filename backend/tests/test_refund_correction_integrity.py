"""Equivalent refund corrections consume the purchase's refundable balance once."""
from datetime import date

import pytest

from app.domain import refunds, transactions
from app.persistence.models import Account, Category, Transaction, TransactionStatus

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
