"""Transfer model: two linked legs, zero-sum, matching (issue #37)."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.transfers import TransferError, create_transfer, legs_balance
from app.persistence.models import (
    Account,
    AccountType,
    Household,
    Transaction,
    Transfer,
)


@pytest.fixture
def accounts(session: Session, household: Household) -> tuple[Account, Account]:
    a = Account(household_id=household.id, name="Joint",
                type=AccountType.joint, currency="GBP",
                opening_balance_pence=0, opening_date=date(2026, 1, 1))
    b = Account(household_id=household.id, name="Bills",
                type=AccountType.bills, currency="GBP",
                opening_balance_pence=0, opening_date=date(2026, 1, 1))
    session.add_all([a, b])
    session.flush()
    return a, b


def _leg(account: Account, pence: int) -> Transaction:
    return Transaction(account_id=account.id, date=date(2026, 4, 1),
                       amount_pence=pence, payee="transfer")


def _pair(a: Account, b: Account, out: int = -50000, into: int = 50000):
    return _leg(a, out), _leg(b, into)


class TestCreate:
    def test_links_both_legs(
        self, session: Session, household: Household,
        accounts: tuple[Account, Account],
    ) -> None:
        a, b = accounts
        debit, credit = _pair(a, b)
        session.add_all([debit, credit])
        session.flush()
        t = create_transfer(session, household.id, debit, credit)
        session.commit()
        assert debit.transfer_id == credit.transfer_id == t.id
        assert len(t.legs) == 2
        assert legs_balance(session, t.id) == 0

    def test_same_account_rejected(
        self, session: Session, household: Household,
        accounts: tuple[Account, Account],
    ) -> None:
        a, _ = accounts
        d, c = _leg(a, -100), _leg(a, 100)
        session.add_all([d, c])
        session.flush()
        with pytest.raises(TransferError, match="different accounts"):
            create_transfer(session, household.id, d, c)

    def test_nonzero_sum_rejected(
        self, session: Session, household: Household,
        accounts: tuple[Account, Account],
    ) -> None:
        a, b = accounts
        d, c = _pair(a, b, out=-50000, into=49999)
        session.add_all([d, c])
        session.flush()
        with pytest.raises(TransferError, match="sum to zero"):
            create_transfer(session, household.id, d, c)

    @pytest.mark.parametrize("out,into", [(500, 500), (-500, -500)])
    def test_wrong_signs_rejected(
        self, session: Session, household: Household,
        accounts: tuple[Account, Account], out: int, into: int,
    ) -> None:
        a, b = accounts
        d, c = _pair(a, b, out=out, into=into)
        session.add_all([d, c])
        session.flush()
        with pytest.raises(TransferError):
            create_transfer(session, household.id, d, c)

    def test_relink_rejected(
        self, session: Session, household: Household,
        accounts: tuple[Account, Account],
    ) -> None:
        a, b = accounts
        d1, c1 = _pair(a, b)
        session.add_all([d1, c1])
        session.flush()
        create_transfer(session, household.id, d1, c1)
        d2, c2 = _pair(a, b)
        session.add_all([d2, c2])
        session.flush()
        with pytest.raises(TransferError, match="already part"):
            create_transfer(session, household.id, d1, c2)


class TestPersistence:
    def test_fk_enforced(self, session: Session, accounts) -> None:
        a, _ = accounts
        t = _leg(a, -100)
        t.transfer_id = 9999
        session.add(t)
        with pytest.raises(IntegrityError):
            session.flush()

    def test_rule_ref_and_unmatched(
        self, session: Session, household: Household,
        accounts: tuple[Account, Account],
    ) -> None:
        a, b = accounts
        d, c = _pair(a, b)
        session.add_all([d, c])
        session.flush()
        t = create_transfer(session, household.id, d, c,
                            matched=False, rule_ref="rule:x")
        assert session.get(Transfer, t.id).matched is False
        assert t.rule_ref == "rule:x"
