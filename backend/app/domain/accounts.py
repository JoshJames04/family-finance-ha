"""Account lifecycle — open, archive/reopen, delete, merge.

An account's opening balance is materialised as a provenance-tagged
system transaction so balances are always derived from the ledger.
Non-empty accounts can never be hard-deleted — merge moves their history
to a surviving account and archives the emptied one.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.domain.expected_matches import eligible_matches
from app.persistence.models import (
    Account,
    AccountType,
    BalanceVerification,
    Employment,
    ExpectedMovement,
    ExpectedSource,
    MergeCandidate,
    RecurringCommitment,
    SavingsGoal,
    Subscription,
    SubscriptionStatus,
    Transaction,
    TransactionSource,
    TransactionStatus,
    TransferRule,
)

OPENING_PAYEE = "Opening balance"
CORRECTION_PAYEE = "Balance correction (manual verification)"


class AccountMergeError(ValueError):
    pass


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
        raise AccountMergeError("cannot merge an account into itself")
    if source.household_id != target.household_id or target.archived:
        raise AccountMergeError("choose an open destination account in the same household")
    moved_bills = select(RecurringCommitment.id).where(
        RecurringCommitment.account_id == source.id)
    bill_occurrences = (
        ExpectedMovement.account_id == source.id,
        ExpectedMovement.source_type == ExpectedSource.commitment,
        ExpectedMovement.source_id.in_(moved_bills),
    )
    today = date.today()
    transfer_rules = select(TransferRule.id).where(
        TransferRule.household_id == source.household_id,
        or_(TransferRule.source_account_id == source.id,
            TransferRule.dest_account_id == source.id),
    )
    other_movements = select(ExpectedMovement.id).where(
        ExpectedMovement.household_id == source.household_id,
        ExpectedMovement.dismissed_at.is_(None),
        or_(ExpectedMovement.account_id == source.id,
            ExpectedMovement.transfer_source_account_id == source.id,
            ExpectedMovement.transfer_dest_account_id == source.id,
            and_(ExpectedMovement.source_type == ExpectedSource.transfer_rule,
                 ExpectedMovement.transfer_source_account_id.is_(None),
                 ExpectedMovement.transfer_dest_account_id.is_(None),
                 ExpectedMovement.source_id.in_(transfer_rules))),
        or_(ExpectedMovement.source_type != ExpectedSource.commitment,
            ExpectedMovement.source_id.not_in(moved_bills)),
    )
    matches = eligible_matches(other_movements)
    paid = select(func.coalesce(func.sum(matches.c.matched_pence), 0)).where(
        matches.c.movement_id == ExpectedMovement.id,
    ).scalar_subquery()
    outstanding = other_movements.where(paid < func.abs(ExpectedMovement.amount_pence))
    # Active definitions also matter when their next occurrence has not materialized yet.
    schedules = (
        outstanding,
        select(Subscription.id).where(Subscription.account_id == source.id,
                                      Subscription.status == SubscriptionStatus.active),
        select(SavingsGoal.id).where(SavingsGoal.account_id == source.id,
                                     SavingsGoal.active.is_(True)),
        select(Employment.id).where(
            Employment.paid_into_account_id == source.id,
            Employment.is_active.is_(True),
            or_(Employment.end_date.is_(None), Employment.end_date >= today),
        ),
        transfer_rules.where(
            TransferRule.active.is_(True),
            or_(TransferRule.end_date.is_(None), TransferRule.end_date >= today),
        ),
    )
    if session.scalar(select(or_(*(query.exists() for query in schedules)))):
        raise AccountMergeError(
            "move or remove the source account's non-bill schedules before merging"
        )
    other = aliased(ExpectedMovement)
    collision = select(other.id).where(
        other.account_id == target.id, other.source_type == ExpectedSource.commitment,
        other.source_id == ExpectedMovement.source_id,
        other.expected_date == ExpectedMovement.expected_date).exists()
    if session.scalar(select(ExpectedMovement.id).where(*bill_occurrences, collision).limit(1)):
        raise AccountMergeError("the destination has an overlapping occurrence of the same bill")
    session.execute(update(ExpectedMovement).where(*bill_occurrences).values(account_id=target.id))
    session.execute(update(MergeCandidate).where(
        MergeCandidate.household_id == source.household_id,
        MergeCandidate.account_id == source.id,
        MergeCandidate.existing_txn_id.in_(select(Transaction.id).where(
            Transaction.account_id == source.id))).values(account_id=target.id))
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


def current_balance(session: Session, account_id: int, *, as_of: date | None = None) -> int:
    """Ledger balance in pence, optionally through a given transaction date."""
    stmt = select(func.coalesce(func.sum(Transaction.amount_pence), 0)).where(
        Transaction.account_id == account_id, Transaction.status != TransactionStatus.void)
    if as_of is not None:
        stmt = stmt.where(Transaction.date <= as_of)
    return int(session.execute(stmt).scalar_one())


def current_movement_account() -> ColumnElement[bool]:
    """A transfer is actionable only while both of its accounts are open."""
    active = select(Account.id).where(
        Account.household_id == ExpectedMovement.household_id,
        Account.archived.is_(False)).correlate(ExpectedMovement)
    movement = ExpectedMovement
    # Confirmed historical transfers can outlive the scheduling rule.
    # Prefer their saved endpoints when present.
    inactive_rule = select(TransferRule.id).where(
        TransferRule.id == movement.source_id,
        TransferRule.household_id == movement.household_id,
        or_(TransferRule.source_account_id.not_in(active),
            TransferRule.dest_account_id.not_in(active))).correlate(movement).exists()
    return and_(
        or_(movement.account_id.is_(None), movement.account_id.in_(active)),
        or_(movement.source_type != ExpectedSource.transfer_rule,
            and_(movement.transfer_source_account_id.in_(active),
                 movement.transfer_dest_account_id.in_(active)),
            and_(movement.transfer_source_account_id.is_(None),
                 movement.transfer_dest_account_id.is_(None), ~inactive_rule)),
    )


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


def verify_balance(
    session: Session,
    account: Account,
    asserted_pence: int,
    verified_date: date,
    *,
    correct: bool = False,
    note: str | None = None,
) -> BalanceVerification:
    """Record an asserted balance against the ledger-derived one.

    When `correct` is set and the asserted balance diverges, a new
    ledger row (source `manual_verification`) is appended — history is
    never edited. The verification links to its adjusting record so the
    corrected balance explains itself.
    """
    derived = current_balance(session, account.id)
    v = BalanceVerification(
        account_id=account.id, asserted_pence=asserted_pence,
        derived_pence=derived, variance_pence=asserted_pence - derived,
        verified_date=verified_date, note=note)
    session.add(v)
    session.flush()
    if correct and v.variance_pence != 0:
        t = Transaction(
            account_id=account.id, date=verified_date,
            amount_pence=v.variance_pence, payee=CORRECTION_PAYEE,
            status=TransactionStatus.reconciled,
            source=TransactionSource.manual_verification,
            external_ref=f"balver:{v.id}",
            notes=note)
        session.add(t)
        session.flush()
        v.correction_txn_id = t.id
        session.flush()
    return v


def verifications(session: Session, account_id: int) -> list[BalanceVerification]:
    return list(session.execute(
        select(BalanceVerification)
        .where(BalanceVerification.account_id == account_id)
        .order_by(BalanceVerification.id.desc())
    ).scalars().all())
