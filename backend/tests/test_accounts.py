"""Account lifecycle: opening, archive, delete-guard, merge (issue #41)."""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.domain.accounts import (
    AccountNotEmptyError,
    active_accounts,
    all_accounts,
    archive,
    current_balance,
    delete_account,
    merge_accounts,
    open_account,
    reopen,
    transaction_count,
)
from app.persistence.models import (
    Account,
    AccountType,
    Household,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

D0 = date(2026, 1, 1)


def _open(session: Session, h: Household, name: str = "A",
          pence: int = 0) -> Account:
    return open_account(session, h.id, name, AccountType.personal,
                        pence, D0)


class TestOpening:
    def test_opening_transaction_created(
        self, session: Session, household: Household
    ) -> None:
        a = _open(session, household, pence=152000)
        session.commit()
        assert transaction_count(session, a.id) == 1
        opening = a.transactions[0]
        assert opening.amount_pence == 152000
        assert opening.source == TransactionSource.system
        assert opening.status == TransactionStatus.reconciled
        assert opening.external_ref == f"open:{a.id}"
        assert current_balance(session, a.id) == 152000

    def test_zero_opening_still_materialised(
        self, session: Session, household: Household
    ) -> None:
        a = _open(session, household)
        session.commit()
        assert transaction_count(session, a.id) == 1


class TestArchive:
    def test_archive_and_reopen(
        self, session: Session, household: Household
    ) -> None:
        a = _open(session, household)
        session.flush()
        archive(session, a)
        assert a.archived is True
        reopen(session, a)
        assert a.archived is False

    def test_archived_excluded_from_active_view(
        self, session: Session, household: Household
    ) -> None:
        a = _open(session, household, "Old")
        b = _open(session, household, "Live")
        archive(session, a)
        assert [x.id for x in active_accounts(session, household.id)] == [b.id]
        assert {x.id for x in all_accounts(session, household.id)} == {
            a.id, b.id}


class TestDelete:
    def test_delete_nonempty_blocked(
        self, session: Session, household: Household
    ) -> None:
        a = _open(session, household)  # has opening txn → non-empty
        session.flush()
        with pytest.raises(AccountNotEmptyError):
            delete_account(session, a)

    def test_delete_empty_ok(
        self, session: Session, household: Household
    ) -> None:
        a = Account(household_id=household.id, name="Ghost",
                    type=AccountType.cash, currency="GBP",
                    opening_balance_pence=0, opening_date=D0)
        session.add(a)
        session.flush()
        delete_account(session, a)
        assert session.get(Account, a.id) is None


class TestMerge:
    def test_merge_moves_history_and_archives(
        self, session: Session, household: Household
    ) -> None:
        src = _open(session, household, "Old", pence=5000)
        tgt = _open(session, household, "New", pence=100)
        extra = Transaction(account_id=src.id, date=date(2026, 2, 1),
                            amount_pence=-200, payee="x")
        session.add(extra)
        session.flush()
        merge_accounts(session, src, tgt)
        session.commit()
        assert src.archived is True
        assert transaction_count(session, src.id) == 0
        # 5000 (moved opening) - 200 (moved txn) + 100 (own opening)
        assert current_balance(session, tgt.id) == 4900

    def test_merge_into_self_rejected(
        self, session: Session, household: Household
    ) -> None:
        a = _open(session, household)
        session.flush()
        with pytest.raises(ValueError, match="itself"):
            merge_accounts(session, a, a)
