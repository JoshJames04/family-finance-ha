"""Issue #429 — automatic bank→occurrence settlement.

ACs: a settled Starling feed item reliably matching an expected
occurrence closes it — ExpectedMatch + schedule advance, one atomic
pass — on every sync trigger (post_sync is the shared funnel);
re-runs are idempotent; ambiguous matches never settle silently.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import contributions, settle
from app.domain import reconcile as recon
from app.persistence.models import (
    Account,
    Confidence,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    Reconciliation,
    ReconStatus,
    RecurringCommitment,
    Subscription,
    SubscriptionPrice,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

TODAY = date(2025, 1, 16)


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Bills",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _commitment(session: Session, household: Household, a: Account,
                *, next_date: date, pence: int = -1599,
                name: str = "Netflix") -> RecurringCommitment:
    c = RecurringCommitment(
        household_id=household.id, name=name, account_id=a.id,
        amount_pence=pence, frequency=Frequency.monthly,
        next_date=next_date)
    session.add(c)
    session.flush()
    return c


def _movement(session: Session, household: Household, a: Account,
              *, on: date, pence: int = -1599, label: str = "Netflix",
              source: ExpectedSource = ExpectedSource.commitment,
              source_id: int = 1) -> ExpectedMovement:
    e = ExpectedMovement(
        household_id=household.id, account_id=a.id,
        expected_date=on, amount_pence=pence, label=label,
        confidence=Confidence.scheduled,
        source_type=source, source_id=source_id)
    session.add(e)
    session.flush()
    return e


def _txn(session: Session, a: Account, on: date, pence: int = -1599,
         payee: str = "Netflix",
         status: TransactionStatus = TransactionStatus.cleared,
         source: TransactionSource = TransactionSource.starling,
         ) -> Transaction:
    t = Transaction(
        account_id=a.id, date=on, amount_pence=pence, payee=payee,
        status=status, source=source)
    session.add(t)
    session.flush()
    return t


def _matches(session: Session) -> list[ExpectedMatch]:
    return list(session.scalars(select(ExpectedMatch)).all())


class TestAutoSettlement:

    def test_cleared_bank_txn_settles_commitment(
            self, session: Session, household: Household,
            account: Account):
        """The core AC: a cleared Starling feed item that reliably
        matches a commitment occurrence writes ExpectedMatch AND
        advances next_date — Bills stops showing it overdue."""
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 15))
        mv = _movement(session, household, account,
                       on=date(2025, 1, 15), source_id=c.id)
        txn = _txn(session, account, date(2025, 1, 15))

        stats = settle.post_sync(session, household.id, today=TODAY)

        assert stats.matched == 1
        match = _matches(session)[0]
        assert match.movement_id == mv.id
        assert match.transaction_id == txn.id
        assert match.matched_pence == 1599
        assert match.manual is False
        session.refresh(c)
        assert c.next_date == date(2025, 2, 15)
        assert c.generated_count == 1
        assert c.last_generated == date(2025, 1, 15)
        session.refresh(txn)
        assert txn.status == TransactionStatus.reconciled
        # provenance: a Reconciliation row records who satisfied what
        rec = session.scalars(select(Reconciliation)).one()
        assert rec.status == ReconStatus.matched
        assert rec.expected_source_id == c.id

    def test_pending_manual_txn_can_settle(
            self, session: Session, household: Household,
            account: Account):
        """A user-entered "I paid this" row is valid payment
        evidence — only system plan rows are excluded."""
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 15))
        _movement(session, household, account,
                  on=date(2025, 1, 15), source_id=c.id)
        _txn(session, account, date(2025, 1, 15),
             status=TransactionStatus.pending,
             source=TransactionSource.manual)

        stats = settle.post_sync(session, household.id, today=TODAY)

        assert stats.matched == 1
        assert len(_matches(session)) == 1
        session.refresh(c)
        assert c.next_date == date(2025, 2, 15)

    def test_system_plan_rows_never_settle(
            self, session: Session, household: Household,
            account: Account):
        """A materialised rec: transaction IS the plan — matching it
        to its own occurrence would fake a payment."""
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 15))
        _movement(session, household, account,
                  on=date(2025, 1, 15), source_id=c.id)
        t = _txn(session, account, date(2025, 1, 15),
                 status=TransactionStatus.pending,
                 source=TransactionSource.system)
        t.external_ref = f"rec:{c.id}:2025-01-15"
        session.flush()

        stats = settle.post_sync(session, household.id, today=TODAY)

        assert stats.matched == 0
        assert stats.queued == 0
        assert _matches(session) == []
        session.refresh(c)
        assert c.next_date == date(2025, 1, 15)

    def test_idempotent_across_repeat_syncs(
            self, session: Session, household: Household,
            account: Account):
        """Repeated syncs / replays never double-settle or
        double-advance."""
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 15))
        mv = _movement(session, household, account,
                       on=date(2025, 1, 15), source_id=c.id)
        _txn(session, account, date(2025, 1, 15))

        settle.post_sync(session, household.id, today=TODAY)
        stats = settle.post_sync(session, household.id, today=TODAY)

        assert stats.matched == 0
        assert stats.queued == 0
        assert len(_matches(session)) == 1
        session.refresh(c)
        assert c.next_date == date(2025, 2, 15)
        assert c.generated_count == 1
        session.refresh(mv)
        assert contributions.status(
            session, mv, TODAY).state == contributions. \
            ContributionState.satisfied

    def test_ambiguous_match_stays_queued(
            self, session: Session, household: Household,
            account: Account):
        """Two equally-scored candidates queue for review — never
        silently settle. No ExpectedMatch, no schedule advance."""
        c1 = _commitment(session, household, account,
                         next_date=date(2025, 1, 15))
        c2 = _commitment(session, household, account,
                         next_date=date(2025, 1, 15))
        _movement(session, household, account,
                  on=date(2025, 1, 15), source_id=c1.id)
        _movement(session, household, account,
                  on=date(2025, 1, 15), source_id=c2.id)
        _txn(session, account, date(2025, 1, 15))

        stats = settle.post_sync(session, household.id, today=TODAY)

        assert stats.matched == 0
        assert stats.queued == 1
        assert _matches(session) == []
        for c in (c1, c2):
            session.refresh(c)
            assert c.next_date == date(2025, 1, 15)

    def test_overpaid_amount_capped(
            self, session: Session, household: Household,
            account: Account):
        """matched_pence caps at the outstanding expected amount —
        an overpaid charge never over-satisfies the movement."""
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 15), pence=-1000)
        _movement(session, household, account,
                  on=date(2025, 1, 15), pence=-1000, source_id=c.id)
        _txn(session, account, date(2025, 1, 15), pence=-1050)

        settle.post_sync(session, household.id, today=TODAY)

        match = _matches(session)[0]
        assert match.matched_pence == 1000

    def test_later_occurrence_match_does_not_skip_head(
            self, session: Session, household: Household,
            account: Account):
        """Matching a movement for a later date while the head is
        still unpaid must not skip the unpaid head occurrence."""
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 15))
        _movement(session, household, account,
                  on=date(2025, 2, 15), source_id=c.id)
        # payment lands for the Feb occurrence while Jan is owed
        _txn(session, account, date(2025, 2, 15))

        settle.post_sync(
            session, household.id, today=date(2025, 2, 20))

        assert len(_matches(session)) == 1
        session.refresh(c)
        # head unchanged — Jan stays outstanding/overdue
        assert c.next_date == date(2025, 1, 15)


class TestConfirmSettles:

    def test_confirm_writes_expected_match(
            self, session: Session, household: Household,
            account: Account):
        """Manual confirm uses the same settlement path — the review
        queue's accept closes the occurrence too."""
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 15))
        mv = _movement(session, household, account,
                       on=date(2025, 1, 15), source_id=c.id)
        txn = _txn(session, account, date(2025, 1, 15),
                   status=TransactionStatus.cleared)
        r = Reconciliation(
            household_id=household.id, account_id=account.id,
            transaction_id=txn.id, status=ReconStatus.pending,
            score=55, candidates={"ranked": []})
        r.expected_source_type = mv.source_type.value
        r.expected_source_id = mv.source_id
        r.expected_label = mv.label
        r.expected_date = mv.expected_date
        r.expected_amount_pence = mv.amount_pence
        r.amount_drift_pence = 0
        r.date_drift_days = 0
        session.add(r)
        session.flush()

        recon.confirm(session, r)

        assert len(_matches(session)) == 1
        session.refresh(c)
        assert c.next_date == date(2025, 2, 15)
        session.refresh(txn)
        assert txn.status == TransactionStatus.reconciled

    def test_confirm_after_rematerialise_resolves_live_row(
            self, session: Session, household: Household,
            account: Account):
        """The snapshot proposed movement may have been deleted and
        re-emitted since the proposal queued — confirm resolves the
        live row by identity, not a stale ORM object."""
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 15))
        old = _movement(session, household, account,
                        on=date(2025, 1, 15), source_id=c.id)
        txn = _txn(session, account, date(2025, 1, 15))
        r = Reconciliation(
            household_id=household.id, account_id=account.id,
            transaction_id=txn.id, status=ReconStatus.pending,
            score=55, candidates={"ranked": []})
        r.expected_source_type = old.source_type.value
        r.expected_source_id = old.source_id
        r.expected_label = old.label
        r.expected_date = old.expected_date
        r.expected_amount_pence = old.amount_pence
        r.amount_drift_pence = 0
        r.date_drift_days = 0
        session.add(r)
        session.delete(old)      # rematerialised away
        session.flush()
        _movement(session, household, account,
                  on=date(2025, 1, 15), source_id=c.id)  # re-emitted

        recon.confirm(session, r)

        assert len(_matches(session)) == 1
        session.refresh(c)
        assert c.next_date == date(2025, 2, 15)


class TestSubscriptionSettlement:

    def test_charge_records_price_and_advances(
            self, session: Session, household: Household,
            account: Account):
        """A matched subscription charge is an observed price —
        record_price advances the renewal and keeps history."""
        sub = Subscription(
            household_id=household.id, payee="Netflix",
            payee_normalised="netflix", expected_pence=1599,
            frequency=Frequency.monthly, anchor_day=15,
            next_renewal_date=date(2025, 1, 15))
        session.add(sub)
        session.flush()
        _movement(session, household, account,
                  on=date(2025, 1, 15), label="Netflix",
                  source=ExpectedSource.subscription,
                  source_id=sub.id)
        txn = _txn(session, account, date(2025, 1, 15))

        stats = settle.post_sync(session, household.id, today=TODAY)

        assert stats.matched == 1
        assert len(_matches(session)) == 1
        price = session.scalars(select(SubscriptionPrice)).one()
        assert price.amount_pence == 1599
        assert price.transaction_id == txn.id
        session.refresh(sub)
        assert sub.next_renewal_date == date(2025, 2, 15)


class TestOutcomeSurfaces:

    def test_settled_movement_leaves_outstanding(
            self, session: Session, household: Household,
            account: Account):
        """The overdue/outstanding surface drops the settled
        movement — the #412 false-overdue fix end to end."""
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 10))
        _movement(session, household, account,
                  on=date(2025, 1, 10), source_id=c.id)
        _txn(session, account, date(2025, 1, 10))
        before = contributions.outstanding(
            session, household.id, today=TODAY)
        assert len(before) == 1

        settle.post_sync(session, household.id, today=TODAY)

        after = contributions.outstanding(
            session, household.id, today=TODAY)
        assert after == []
        session.refresh(c)
        assert c.next_date == date(2025, 2, 10)

    def test_unmatched_past_due_stays_overdue(
            self, session: Session, household: Household,
            account: Account):
        """Genuine unpaid occurrences still surface — settlement
        hides only what the bank actually paid."""
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 10))
        _movement(session, household, account,
                  on=date(2025, 1, 10), source_id=c.id)

        settle.post_sync(session, household.id, today=TODAY)

        assert len(contributions.outstanding(
            session, household.id, today=TODAY)) == 1
        session.refresh(c)
        assert c.next_date == date(2025, 1, 10)

    def test_dry_run_reports_without_persisting(
            self, session: Session, household: Household,
            account: Account):
        """POST /reconcile/accounts/{id}/run?dry_run=true is the
        report-first backfill path — same counts, no writes."""
        from app.api.v1.reconcile import run as run_route
        c = _commitment(session, household, account,
                        next_date=date(2025, 1, 15))
        _movement(session, household, account,
                  on=date(2025, 1, 15), source_id=c.id)
        _txn(session, account, date(2025, 1, 15))
        session.commit()

        stats = run_route(account.id, household, session, "test",
                          dry_run=True)

        assert stats.matched == 1
        assert _matches(session) == []
        assert list(session.scalars(select(Reconciliation))) == []
        session.refresh(c)
        assert c.next_date == date(2025, 1, 15)
