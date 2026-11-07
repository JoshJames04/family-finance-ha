"""Reporting periods + reconciliation mutation gates (issue #40)."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.periods import (
    PeriodLockedError,
    ReconciledImmutableError,
    assert_editable,
    lock_period,
    open_period,
    period_for,
    transition,
)
from app.domain.transactions import InvalidTransitionError
from app.persistence.models import (
    Account,
    AccountType,
    Household,
    Transaction,
    TransactionStatus,
)


@pytest.fixture
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Joint",
                type=AccountType.joint, currency="GBP",
                opening_balance_pence=0, opening_date=date(2026, 1, 1))
    session.add(a)
    session.flush()
    return a


def _txn(account: Account, d: date = date(2026, 5, 10),
         pence: int = -100) -> Transaction:
    return Transaction(account_id=account.id, date=d,
                       amount_pence=pence, payee="p")


class TestPeriods:
    def test_open_and_find(
        self, session: Session, household: Household
    ) -> None:
        p = open_period(session, household.id, "2026-05",
                        date(2026, 5, 1), date(2026, 5, 31))
        assert period_for(session, household.id, date(2026, 5, 15)) == p
        assert period_for(session, household.id, date(2026, 6, 1)) is None

    def test_label_unique_per_household(
        self, session: Session, household: Household
    ) -> None:
        open_period(session, household.id, "2026-05",
                    date(2026, 5, 1), date(2026, 5, 31))
        with pytest.raises(IntegrityError):
            open_period(session, household.id, "2026-05",
                        date(2026, 5, 1), date(2026, 5, 30))

    def test_bad_range_rejected(
        self, session: Session, household: Household
    ) -> None:
        with pytest.raises(IntegrityError):
            open_period(session, household.id, "bad",
                        date(2026, 6, 1), date(2026, 5, 1))


class TestTransitionGate:
    def test_valid_transition(
        self, session: Session, account: Account
    ) -> None:
        t = _txn(account)
        session.add(t)
        session.flush()
        transition(session, t, TransactionStatus.cleared)
        transition(session, t, TransactionStatus.reconciled)
        assert t.status == TransactionStatus.reconciled

    def test_invalid_transition(
        self, session: Session, account: Account
    ) -> None:
        t = _txn(account)
        session.add(t)
        session.flush()
        with pytest.raises(InvalidTransitionError):
            transition(session, t, TransactionStatus.reconciled)

    def test_transition_blocked_in_locked_period(
        self, session: Session, account: Account, household: Household
    ) -> None:
        p = open_period(session, household.id, "2026-05",
                        date(2026, 5, 1), date(2026, 5, 31))
        t = _txn(account, date(2026, 5, 10))
        session.add(t)
        session.flush()
        lock_period(session, p)
        with pytest.raises(PeriodLockedError):
            transition(session, t, TransactionStatus.void)

    def test_transition_outside_period_ok(
        self, session: Session, account: Account, household: Household
    ) -> None:
        p = open_period(session, household.id, "2026-05",
                        date(2026, 5, 1), date(2026, 5, 31))
        lock_period(session, p)
        t = _txn(account, date(2026, 6, 2))
        session.add(t)
        session.flush()
        transition(session, t, TransactionStatus.cleared)
        assert t.status == TransactionStatus.cleared


class TestEditability:
    def test_reconciled_immutable(
        self, session: Session, account: Account
    ) -> None:
        t = _txn(account)
        session.add(t)
        session.flush()
        transition(session, t, TransactionStatus.cleared)
        transition(session, t, TransactionStatus.reconciled)
        with pytest.raises(ReconciledImmutableError):
            assert_editable(session, t)

    def test_editable_when_open(
        self, session: Session, account: Account
    ) -> None:
        t = _txn(account)
        session.add(t)
        session.flush()
        assert_editable(session, t)  # pending, no locked period


class TestLockReconciles:
    def test_lock_marks_cleared_reconciled(
        self, session: Session, account: Account, household: Household
    ) -> None:
        p = open_period(session, household.id, "2026-05",
                        date(2026, 5, 1), date(2026, 5, 31))
        cleared = _txn(account, date(2026, 5, 5))
        pending = _txn(account, date(2026, 5, 6))
        outside = _txn(account, date(2026, 6, 1))
        session.add_all([cleared, pending, outside])
        session.flush()
        transition(session, cleared, TransactionStatus.cleared)
        transition(session, outside, TransactionStatus.cleared)
        lock_period(session, p)
        session.refresh(cleared)
        session.refresh(pending)
        session.refresh(outside)
        assert cleared.status == TransactionStatus.reconciled
        assert pending.status == TransactionStatus.pending
        assert outside.status == TransactionStatus.cleared
        assert p.locked is True
