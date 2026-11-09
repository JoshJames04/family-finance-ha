"""Issue #69 — reconciliation of transactions against expected
movements.

ACs: an expected bill matched to a feed item closes the loop
(reconciled state); partial/ambiguous matches never auto-resolve
silently — they queue for review with ranked candidates.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import reconcile as recon
from app.persistence.models import (
    Account,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Household,
    Reconciliation,
    ReconStatus,
    Transaction,
    TransactionSource,
    TransactionStatus,
)


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Bills",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _expected(session: Session, household: Household, a: Account,
              on: date, pence: int, label: str,
              source_id: int = 1) -> ExpectedMovement:
    e = ExpectedMovement(
        household_id=household.id, account_id=a.id,
        expected_date=on, amount_pence=pence, label=label,
        confidence=Confidence.scheduled,
        source_type=ExpectedSource.commitment, source_id=source_id)
    session.add(e)
    session.flush()
    return e


def _txn(session: Session, a: Account, on: date, pence: int,
         payee: str = "Shop") -> Transaction:
    t = Transaction(
        account_id=a.id, date=on, amount_pence=pence, payee=payee,
        status=TransactionStatus.pending,
        source=TransactionSource.starling)
    session.add(t)
    session.flush()
    return t


class TestAutoMatch:
    def test_expected_bill_matched_closes_loop(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: exact expected bill + matching feed item auto-matches
        and the transaction transitions to reconciled."""
        _expected(session, household, account,
                  date(2025, 5, 3), -45_000, "Council Tax")
        t = _txn(session, account, date(2025, 5, 3), -45_000,
                 "Council Tax")
        stats = recon.reconcile_account(session, account,
                                        date(2025, 5, 4))
        assert (stats.scanned, stats.matched, stats.queued) == (
            1, 1, 0)
        assert t.status == TransactionStatus.reconciled
        r = session.scalar(select(Reconciliation))
        assert r is not None
        assert r.status == ReconStatus.matched
        assert r.expected_label == "Council Tax"
        assert r.amount_drift_pence == 0
        assert r.date_drift_days == 0

    def test_expected_only_matches_once(
            self, session: Session, household: Household,
            account: Account) -> None:
        """Two identical transactions can't both claim one expected
        row — the first match wins, the second queues/none."""
        _expected(session, household, account,
                  date(2025, 5, 3), -45_000, "Council Tax")
        _txn(session, account, date(2025, 5, 3), -45_000, "C T")
        _txn(session, account, date(2025, 5, 3), -45_000, "C T")
        stats = recon.reconcile_account(session, account,
                                        date(2025, 5, 4))
        assert stats.matched == 1


class TestAmbiguity:
    def test_near_identical_candidates_queue_not_auto(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: ambiguous partial matches never auto-resolve — they
        queue with ranked candidates."""
        _expected(session, household, account,
                  date(2025, 5, 3), -45_000, "Netflix A", 1)
        _expected(session, household, account,
                  date(2025, 5, 3), -45_000, "Netflix B", 2)
        t = _txn(session, account, date(2025, 5, 3), -45_000,
                 "Netflix")
        stats = recon.reconcile_account(session, account,
                                        date(2025, 5, 4))
        assert stats.matched == 0
        assert stats.queued == 1
        assert t.status == TransactionStatus.pending
        r = session.scalar(select(Reconciliation))
        assert r is not None
        assert r.status == ReconStatus.pending
        assert len(r.candidates["ranked"]) == 1  # runner-up stored

    def test_weak_match_queues(self, session: Session,
                               household: Household,
                               account: Account) -> None:
        """A plausible-but-imperfect match (drifted amount+date)
        still beats nothing: it queues, recording the drift."""
        _expected(session, household, account,
                  date(2025, 5, 1), -45_000, "Gym")
        _txn(session, account, date(2025, 5, 6), -46_500, "Gym")
        stats = recon.reconcile_account(session, account,
                                        date(2025, 5, 10))
        assert stats.queued == 1
        r = session.scalar(select(Reconciliation))
        assert r is not None
        assert r.status == ReconStatus.pending
        assert r.amount_drift_pence == -1_500
        assert r.date_drift_days == 5

    def test_no_candidate_leaves_alone(
            self, session: Session, household: Household,
            account: Account) -> None:
        _txn(session, account, date(2025, 5, 3), -45_000, "Random")
        stats = recon.reconcile_account(session, account,
                                        date(2025, 5, 4))
        assert (stats.matched, stats.queued) == (0, 0)
        assert session.scalar(select(Reconciliation)) is None

    def test_sign_mismatch_never_matches(
            self, session: Session, household: Household,
            account: Account) -> None:
        """An income never reconciles against an expected expense."""
        _expected(session, household, account,
                  date(2025, 5, 3), -45_000, "Bill")
        _txn(session, account, date(2025, 5, 3), 45_000, "Bill")
        stats = recon.reconcile_account(session, account,
                                        date(2025, 5, 4))
        assert (stats.matched, stats.queued) == (0, 0)


class TestReviewLifecycle:
    def test_confirm_then_rescan_is_stable(
            self, session: Session, household: Household,
            account: Account) -> None:
        _expected(session, household, account,
                  date(2025, 5, 3), -45_000, "X A", 1)
        _expected(session, household, account,
                  date(2025, 5, 3), -45_000, "X B", 2)
        t = _txn(session, account, date(2025, 5, 3), -45_000, "X")
        recon.reconcile_account(session, account, date(2025, 5, 4))
        r = session.scalar(select(Reconciliation))
        assert r is not None
        recon.confirm(session, r)
        assert t.status == TransactionStatus.reconciled
        # Re-running must not create a second row for the txn.
        stats = recon.reconcile_account(session, account,
                                        date(2025, 5, 5))
        assert stats.matched == 0

    def test_dismiss_rescans(self, session: Session,
                           household: Household,
                           account: Account) -> None:
        _expected(session, household, account,
                  date(2025, 5, 3), -45_000, "X A", 1)
        _expected(session, household, account,
                  date(2025, 5, 3), -45_000, "X B", 2)
        _txn(session, account, date(2025, 5, 3), -45_000, "X")
        recon.reconcile_account(session, account, date(2025, 5, 4))
        r = session.scalar(select(Reconciliation))
        assert r is not None
        recon.dismiss(session, r)
        assert r.status == ReconStatus.dismissed
        stats = recon.reconcile_account(session, account,
                                        date(2025, 5, 5))
        assert stats.queued == 1  # re-scored into a fresh row
