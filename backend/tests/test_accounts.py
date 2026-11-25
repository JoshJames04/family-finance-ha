"""Account lifecycle: opening, archive, delete-guard, merge (issue #41)."""

from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.domain.accounts import (
    AccountMergeError,
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
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    Subscription,
    Transaction,
    TransactionSource,
    TransactionStatus,
    TransferRule,
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

    def test_voided_rows_never_count(
        self, session: Session, household: Household
    ) -> None:
        """Issue #347 — current_balance excludes status='void'."""
        a = _open(session, household, pence=1000)
        session.add_all([
            Transaction(account_id=a.id, date=D0, amount_pence=-400,
                        payee="kept", status=TransactionStatus.cleared,
                        source=TransactionSource.manual),
            Transaction(account_id=a.id, date=D0, amount_pence=-99900,
                        payee="voided", status=TransactionStatus.void,
                        source=TransactionSource.manual)])
        session.flush()
        assert current_balance(session, a.id) == 600


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

    @pytest.mark.parametrize("schedule_type", ["manual", "subscription", "transfer"])
    def test_merge_rejects_unmoved_schedules_before_changes(
        self, session: Session, household: Household, schedule_type: str,
    ) -> None:
        source = _open(session, household, "Old", pence=5000)
        target = _open(session, household, "New", pence=100)
        future = date.today() + timedelta(days=180)
        if schedule_type == "manual":
            session.add(ExpectedMovement(
                household_id=household.id, account_id=source.id, expected_date=future,
                amount_pence=-200, label="Planned expense", confidence=Confidence.scheduled,
                source_type=ExpectedSource.manual, source_id=1,
            ))
        elif schedule_type == "subscription":
            session.add(Subscription(
                household_id=household.id, payee="Test", payee_normalised="test",
                account_id=source.id, frequency=Frequency.monthly, expected_pence=200,
                next_renewal_date=future, anchor_day=1,
            ))
        else:
            session.add(TransferRule(
                household_id=household.id, name="Recurring transfer",
                source_account_id=target.id, dest_account_id=source.id,
                amount_spec='{"kind":"fixed","pence":200}', frequency=Frequency.monthly,
                day_of_month=1, start_date=future,
            ))
        session.flush()

        with pytest.raises(AccountMergeError, match="non-bill schedules"):
            merge_accounts(session, source, target)

        assert not source.archived
        assert current_balance(session, source.id) == 5000
        assert current_balance(session, target.id) == 100

    def test_merge_into_self_rejected(
        self, session: Session, household: Household
    ) -> None:
        a = _open(session, household)
        session.flush()
        with pytest.raises(ValueError, match="itself"):
            merge_accounts(session, a, a)
