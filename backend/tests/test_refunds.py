"""Refunds/reimbursements: linking, partials, netting (issue #39)."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.refunds import (
    RefundError,
    category_spend,
    link_refund,
    refundable_balance,
)
from app.persistence.models import (
    Account,
    AccountType,
    Category,
    Household,
    Transaction,
)


@pytest.fixture
def accounts(session: Session, household: Household) -> tuple[Account, Account]:
    a = Account(household_id=household.id, name="Personal",
                type=AccountType.personal, currency="GBP",
                opening_balance_pence=0, opening_date=date(2026, 1, 1))
    b = Account(household_id=household.id, name="Joint",
                type=AccountType.joint, currency="GBP",
                opening_balance_pence=0, opening_date=date(2026, 1, 1))
    session.add_all([a, b])
    session.flush()
    return a, b


@pytest.fixture
def cat(session: Session, household: Household) -> Category:
    c = Category(household_id=household.id, name="Groceries")
    session.add(c)
    session.flush()
    return c


def _txn(account: Account, pence: int, cat: Category | None = None,
         **kw) -> Transaction:
    return Transaction(account_id=account.id, date=date(2026, 5, 10),
                       amount_pence=pence, payee="p",
                       category_id=cat.id if cat else None, **kw)


class TestLinking:
    def test_full_refund(
        self, session: Session, accounts, cat: Category
    ) -> None:
        a, _ = accounts
        orig = _txn(a, -2500, cat)
        ref = _txn(a, 2500, cat)
        session.add_all([orig, ref])
        session.flush()
        link_refund(session, ref, orig)
        assert ref.refund_of_id == orig.id
        assert refundable_balance(session, orig.id) == 0

    def test_partial_refunds(
        self, session: Session, accounts, cat: Category
    ) -> None:
        a, _ = accounts
        orig = _txn(a, -10000, cat)
        session.add(orig)
        session.flush()
        for amt in (3000, 4000):
            r = _txn(a, amt, cat)
            session.add(r)
            session.flush()
            link_refund(session, r, orig)
        assert refundable_balance(session, orig.id) == 3000

    def test_over_refund_rejected(
        self, session: Session, accounts, cat: Category
    ) -> None:
        a, _ = accounts
        orig = _txn(a, -5000, cat)
        r1, r2 = _txn(a, 4000, cat), _txn(a, 2000, cat)
        session.add_all([orig, r1])
        session.flush()
        link_refund(session, r1, orig)
        session.add(r2)
        session.flush()
        with pytest.raises(RefundError, match="exceeds"):
            link_refund(session, r2, orig)

    def test_cannot_refund_inflow(
        self, session: Session, accounts
    ) -> None:
        a, _ = accounts
        orig = _txn(a, 1000)
        r = _txn(a, 500)
        session.add_all([orig, r])
        session.flush()
        with pytest.raises(RefundError, match="outflow"):
            link_refund(session, r, orig)

    def test_cannot_refund_a_refund(
        self, session: Session, accounts, cat: Category
    ) -> None:
        a, _ = accounts
        orig = _txn(a, -1000, cat)
        r1 = _txn(a, 1000, cat)
        session.add_all([orig, r1])
        session.flush()
        link_refund(session, r1, orig)
        r2 = _txn(a, -500, cat)
        r3 = _txn(a, 500, cat)
        session.add_all([r2, r3])
        session.flush()
        link_refund(session, r3, r2)
        r4 = _txn(a, 200, cat)
        session.add(r4)
        session.flush()
        # refunds are inflows, so a refund-of-refund attempt is caught
        # by the outflow guard
        with pytest.raises(RefundError, match="outflow"):
            link_refund(session, r4, r3)

    def test_negative_refund_rejected(
        self, session: Session, accounts
    ) -> None:
        a, _ = accounts
        orig = _txn(a, -1000)
        r = _txn(a, -200)
        session.add_all([orig, r])
        session.flush()
        with pytest.raises(RefundError, match="positive"):
            link_refund(session, r, orig)

    def test_cross_account_reimbursement(
        self, session: Session, accounts, cat: Category
    ) -> None:
        """Spend on personal, reimbursed into joint — different accounts."""
        a, b = accounts
        orig = _txn(a, -8000, cat)
        ref = _txn(b, 8000, cat)
        session.add_all([orig, ref])
        session.flush()
        link_refund(session, ref, orig)
        assert ref.account_id != orig.account_id
        assert ref.refund_of_id == orig.id

    def test_refund_of_fk_enforced(
        self, session: Session, accounts
    ) -> None:
        a, _ = accounts
        t = _txn(a, 100)
        t.refund_of_id = 424242
        session.add(t)
        with pytest.raises(IntegrityError):
            session.flush()


class TestNetting:
    def test_gross_vs_net(
        self, session: Session, accounts, cat: Category
    ) -> None:
        a, _ = accounts
        spend = _txn(a, -10000, cat)
        session.add(spend)
        session.flush()
        refund = _txn(a, 3000, cat)
        session.add(refund)
        session.flush()
        link_refund(session, refund, spend)
        s = category_spend(session, cat.id, date(2026, 5, 1),
                           date(2026, 5, 31))
        g = category_spend(session, cat.id, date(2026, 5, 1),
                           date(2026, 5, 31), net=False)
        assert s == -7000   # net
        assert g == -10000  # gross

    def test_period_bounds(
        self, session: Session, accounts, cat: Category
    ) -> None:
        a, _ = accounts
        session.add_all([
            _txn(a, -100, cat),
            Transaction(account_id=a.id, date=date(2026, 6, 1),
                        amount_pence=-999, payee="later",
                        category_id=cat.id),
        ])
        session.commit()
        assert category_spend(session, cat.id, date(2026, 5, 1),
                              date(2026, 5, 31)) == -100
