"""Issue #114 — unusual transaction detection.

ACs: planted outlier flagged with correct trigger + always-present
explanation; normal spend unflagged; dismissals tune suppression.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import anomaly
from app.persistence.models import (
    Account,
    AnomalyFlag,
    AnomalyStatus,
    AnomalyTrigger,
    Category,
    Household,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

OLD = date(2025, 1, 1)
TODAY = date(2025, 4, 15)


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Current",
                type="personal", opening_balance_pence=0,
                opening_date=OLD)
    session.add(a)
    session.flush()
    return a


def _txn(session: Session, a: Account, on: date, pence: int,
         payee: str, category_id: int | None = None,
         status: TransactionStatus = TransactionStatus.cleared
         ) -> Transaction:
    t = Transaction(account_id=a.id, date=on, amount_pence=pence,
                    payee=payee, status=status,
                    source=TransactionSource.manual,
                    category_id=category_id)
    session.add(t)
    session.flush()
    return t


def _history(session: Session, a: Account, payee: str,
             amounts: list[int], category_id: int | None = None
             ) -> None:
    """Evenly-spaced historical spend before the scan window."""
    for i, amt in enumerate(amounts):
        _txn(session, a,
             date.fromordinal(OLD.toordinal() + 1 + i * 7),
             -amt, payee, category_id)


def _flags(session: Session, a: Account) -> list[AnomalyFlag]:
    return list(session.scalars(
        select(AnomalyFlag).where(
            AnomalyFlag.account_id == a.id)))


class TestAmountOutlier:
    def test_planted_outlier_flagged_with_explanation(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: £45-typical payee charging £400 → amount_outlier
        flag with computed evidence."""
        _history(session, account, "Tesco",
                 [4_500, 4_200, 4_800, 4_300, 4_600])
        big = _txn(session, account, date(2025, 4, 10), -40_000,
                   "Tesco")
        stats = anomaly.scan_account(session, account, TODAY)
        assert stats.flagged >= 1
        f = next(f for f in _flags(session, account)
                 if f.transaction_id == big.id
                 and f.trigger == AnomalyTrigger.amount_outlier)
        d = f.detail
        assert d["amount_pence"] == 40_000
        assert d["threshold"] < 40_000
        assert d["payee_median"] == pytest.approx(4_500)
        assert d["samples"] == 5
        assert f.status == AnomalyStatus.pending

    def test_normal_spend_unflagged(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: in-band spend produces zero flags."""
        _history(session, account, "Tesco",
                 [4_500, 4_200, 4_800, 4_300, 4_600])
        _txn(session, account, date(2025, 4, 10), -4_700, "Tesco")
        stats = anomaly.scan_account(session, account, TODAY)
        assert stats.flagged == 0
        assert _flags(session, account) == []


class TestCategoryOutlier:
    def test_category_outlier_fires(
            self, session: Session, household: Household,
            account: Account) -> None:
        cat = Category(household_id=household.id,
                       name="Groceries")
        session.add(cat)
        session.flush()
        _history(session, account, "Shops",
                 [5_000, 5_200, 4_800, 5_100, 5_300], cat.id)
        big = _txn(session, account, date(2025, 4, 10), -45_000,
                   "Shops", cat.id)
        anomaly.scan_account(session, account, TODAY)
        f = next(f for f in _flags(session, account)
                 if f.transaction_id == big.id
                 and f.trigger == AnomalyTrigger.category_outlier)
        assert f.detail["category_id"] == cat.id


class TestNewPayee:
    def test_new_payee_large_flagged(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: unseen payee + large amount → new_payee_large."""
        for i in range(10):
            _txn(session, account,
                 date.fromordinal(OLD.toordinal() + 1 + i * 5),
                 -(2_000 + i * 300), f"Shop{i}")
        new = _txn(session, account, date(2025, 4, 10), -30_000,
                   "NeverSeen Ltd")
        anomaly.scan_account(session, account, TODAY)
        f = next(f for f in _flags(session, account)
                 if f.transaction_id == new.id
                 and f.trigger == AnomalyTrigger.new_payee_large)
        assert f.detail["history_p95"] <= 30_000

    def test_new_payee_small_not_flagged(
            self, session: Session, household: Household,
            account: Account) -> None:
        for i in range(10):
            _txn(session, account,
                 date.fromordinal(OLD.toordinal() + 1 + i * 5),
                 -(2_000 + i * 300), f"Shop{i}")
        _txn(session, account, date(2025, 4, 10), -500,
             "Small New Shop")
        stats = anomaly.scan_account(session, account, TODAY)
        assert stats.flagged == 0


class TestBurst:
    def test_three_same_day_flagged(
            self, session: Session, household: Household,
            account: Account) -> None:
        for i in range(4):
            _txn(session, account,
                 date.fromordinal(OLD.toordinal() + 4 + i * 10),
                 -1_200, "Cafe")
        d = date(2025, 4, 10)
        for _ in range(3):
            _txn(session, account, d, -1_200, "Cafe")
        anomaly.scan_account(session, account, TODAY)
        burst = [f for f in _flags(session, account)
                 if f.trigger == AnomalyTrigger.burst]
        assert len(burst) == 3
        assert burst[0].detail["same_day_count"] == 3


class TestFeedback:
    def test_dismissals_suppress_pair(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: ≥2 dismissals of (trigger, payee) → future flags
        for that pair suppressed."""
        _history(session, account, "Tesco",
                 [4_500, 4_200, 4_800, 4_300, 4_600])
        for _ in range(2):
            big = _txn(session, account, date(2025, 4, 10),
                       -40_000, "Tesco")
            anomaly.scan_account(session, account, TODAY)
            f = next(f2 for f2 in _flags(session, account)
                     if f2.transaction_id == big.id
                     and f2.trigger
                     == AnomalyTrigger.amount_outlier)
            f.status = AnomalyStatus.dismissed
            session.flush()
        third = _txn(session, account, date(2025, 4, 12),
                     -40_000, "Tesco")
        stats = anomaly.scan_account(session, account, TODAY)
        assert stats.suppressed >= 1
        assert not any(f.transaction_id == third.id
                       for f in _flags(session, account))
