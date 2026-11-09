"""Transaction model + lifecycle rules (issue #36)."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.transactions import (
    InvalidTransitionError,
    assert_transition,
    can_transition,
)
from app.persistence.models import (
    Account,
    AccountType,
    Category,
    Household,
    Transaction,
    TransactionSource,
    TransactionStatus,
)


@pytest.fixture
def account(session: Session) -> Account:
    h = Household(name="Test Household")
    a = Account(
        household=h, name="Joint", type=AccountType.joint,
        currency="GBP", opening_balance_pence=0,
        opening_date=date(2026, 1, 1),
    )
    session.add_all([h, a])
    session.flush()
    return a


def _txn(account: Account, **kw) -> Transaction:
    return Transaction(
        account_id=account.id,
        date=date(2026, 3, 5),
        amount_pence=kw.pop("amount_pence", -1299),
        payee=kw.pop("payee", "Tesco"),
        **kw,
    )


class TestPersistence:
    def test_roundtrip(self, session: Session, account: Account) -> None:
        t = _txn(account)
        session.add(t)
        session.commit()
        got = session.get(Transaction, t.id)
        assert got is not None
        assert got.amount_pence == -1299
        assert got.status == TransactionStatus.pending
        assert got.source == TransactionSource.manual
        assert got.account.name == "Joint"

    def test_account_relationship(
        self, session: Session, account: Account
    ) -> None:
        session.add_all([_txn(account), _txn(account, payee="Asda")])
        session.commit()
        assert {t.payee for t in account.transactions} == {"Tesco", "Asda"}

    def test_category_link(
        self, session: Session, account: Account
    ) -> None:
        c = Category(household_id=account.household_id, name="Groceries")
        session.add(c)
        session.flush()
        t = _txn(account, category_id=c.id)
        session.add(t)
        session.commit()
        assert t.category is not None and t.category.name == "Groceries"

    def test_signed_amounts(self, session: Session, account: Account) -> None:
        session.add_all([
            _txn(account, amount_pence=-500),
            _txn(account, amount_pence=320000),
        ])
        session.commit()
        assert sorted(t.amount_pence for t in account.transactions) == [
            -500, 320000]

    def test_dedup_key(
        self, session: Session, account: Account
    ) -> None:
        kw = {"source": TransactionSource.starling, "external_ref": "stg-1"}
        session.add(_txn(account, **kw))
        session.commit()
        session.add(_txn(account, **kw))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_null_external_ref_not_deduped(
        self, session: Session, account: Account
    ) -> None:
        session.add_all([_txn(account), _txn(account)])
        session.commit()  # no ref → no dedup collision

    def test_invalid_status_rejected(
        self, session: Session, account: Account
    ) -> None:
        session.add(_txn(account, status="bogus"))  # type: ignore[arg-type]
        with pytest.raises(IntegrityError):
            session.flush()


class TestLifecycle:
    @pytest.mark.parametrize("frm,to,ok", [
        ("pending", "cleared", True),
        ("pending", "reconciled", False),
        ("pending", "void", True),
        ("cleared", "reconciled", True),
        ("cleared", "pending", True),
        ("cleared", "void", True),
        ("reconciled", "cleared", False),
        ("reconciled", "void", False),
        ("void", "pending", False),
    ])
    def test_matrix(self, frm: str, to: str, ok: bool) -> None:
        assert can_transition(TransactionStatus(frm),
                              TransactionStatus(to)) is ok

    def test_assert_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            assert_transition(TransactionStatus.reconciled,
                              TransactionStatus.pending)

    def test_happy_path(self) -> None:
        assert_transition(TransactionStatus.pending,
                          TransactionStatus.cleared)
        assert_transition(TransactionStatus.cleared,
                          TransactionStatus.reconciled)


class TestCorrection:
    """Issue #124: reconciled rows are corrected via a superseding row,
    never edited; the original voids and the replacement re-enters
    review as pending."""

    def test_correct_supersedes(self, session: Session,
                                account: Account) -> None:
        from app.domain import transactions as dom
        t = _txn(account, status=TransactionStatus.reconciled,
                 category_id=None)
        session.add(t)
        session.flush()
        corrected = dom.correct(session, t, "wrong payee",
                                payee="Sainsbury's")
        assert t.status is TransactionStatus.void
        assert corrected.status is TransactionStatus.pending
        assert corrected.corrects_id == t.id
        assert corrected.payee == "Sainsbury's"
        assert corrected.amount_pence == t.amount_pence
        assert "wrong payee" in (corrected.notes or "")
        assert corrected.id != t.id

    def test_correct_keeps_fields(self, session: Session,
                                  account: Account) -> None:
        from app.domain import transactions as dom
        cat = Category(household_id=account.household_id, name="Groceries")
        session.add(cat)
        session.flush()
        t = _txn(account, status=TransactionStatus.reconciled,
                 category_id=cat.id)
        session.add(t)
        session.flush()
        corrected = dom.correct(session, t, "recat only")
        assert corrected.payee == t.payee
        assert corrected.category_id == cat.id

    def test_non_reconciled_rejected(self, session: Session,
                                     account: Account) -> None:
        from app.domain import transactions as dom
        from app.domain.transactions import CorrectionError
        t = _txn(account, status=TransactionStatus.pending)
        session.add(t)
        session.flush()
        with pytest.raises(CorrectionError):
            dom.correct(session, t, "not needed")
