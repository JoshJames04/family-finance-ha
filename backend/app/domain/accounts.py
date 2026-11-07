"""Account lifecycle — open, archive/reopen, delete, merge.

An account's opening balance is materialised as a provenance-tagged
system transaction so balances are always derived from the ledger.
Non-empty accounts can never be hard-deleted — merge moves their history
to a surviving account and archives the emptied one.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.persistence.models import (
    Account,
    AccountType,
    RecurringCommitment,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

OPENING_PAYEE = "Opening balance"


class AccountNotEmptyError(RuntimeError):
    pass


def open_account(
    session: Session,
    household_id: int,
    name: str,
    account_type: AccountType,
    opening_balance_pence: int,
    opening_date: date,
    **kw: Any,
) -> Account:
    """Create an account plus its synthetic opening transaction."""
    a = Account(
        household_id=household_id, name=name, type=account_type,
        currency="GBP", opening_balance_pence=opening_balance_pence,
        opening_date=opening_date, **kw,
    )
    session.add(a)
    session.flush()
    session.add(Transaction(
        account_id=a.id, date=opening_date,
        amount_pence=opening_balance_pence, payee=OPENING_PAYEE,
        status=TransactionStatus.reconciled,
        source=TransactionSource.system,
        external_ref=f"open:{a.id}",
    ))
    session.flush()
    return a


def archive(session: Session, account: Account) -> None:
    account.archived = True
    session.flush()


def reopen(session: Session, account: Account) -> None:
    account.archived = False
    session.flush()


def transaction_count(session: Session, account_id: int) -> int:
    return int(session.execute(
        select(func.count(Transaction.id)).where(
            Transaction.account_id == account_id)
    ).scalar_one())


def delete_account(session: Session, account: Account) -> None:
    """Hard-delete is only legal for a truly empty account."""
    if transaction_count(session, account.id) > 0:
        raise AccountNotEmptyError(
            f"account {account.id} has history — merge or archive instead"
        )
    session.delete(account)
    session.flush()


def merge_accounts(
    session: Session, source: Account, target: Account,
) -> None:
    """Move all history from `source` to `target`, then archive source."""
    if source.id == target.id:
        raise ValueError("cannot merge an account into itself")
    session.execute(
        update(Transaction)
        .where(Transaction.account_id == source.id)
        .values(account_id=target.id)
    )
    session.execute(
        update(RecurringCommitment)
        .where(RecurringCommitment.account_id == source.id)
        .values(account_id=target.id)
    )
    session.flush()
    archive(session, source)


def current_balance(session: Session, account_id: int) -> int:
    """Derived from the ledger (opening row included) — pence."""
    return int(session.execute(
        select(func.coalesce(func.sum(Transaction.amount_pence), 0))
        .where(Transaction.account_id == account_id)
    ).scalar_one())


def active_accounts(session: Session, household_id: int) -> list[Account]:
    """Dashboard view — archived accounts excluded."""
    return list(session.execute(
        select(Account).where(
            Account.household_id == household_id,
            Account.archived.is_(False),
        )
    ).scalars().all())


def all_accounts(session: Session, household_id: int) -> list[Account]:
    """History view — includes archived."""
    return list(session.execute(
        select(Account).where(Account.household_id == household_id)
    ).scalars().all())
