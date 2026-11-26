"""Recurring commitments: scheduling, end conditions, materialise (issue #38)."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain import contributions, reconcile, schedule, settle
from app.domain.recurring import advance, materialise, occurrences, rescheduled_occurrences
from app.persistence.models import (
    Account,
    AccountType,
    ExpectedMovement,
    Frequency,
    Household,
    MergeCandidate,
    Reconciliation,
    RecurringCommitment,
    Transaction,
)


@pytest.fixture
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Bills",
                type=AccountType.bills, currency="GBP",
                opening_balance_pence=0, opening_date=date(2026, 1, 1))
    session.add(a)
    session.flush()
    return a


def _rc(account: Account, **kw) -> RecurringCommitment:
    return RecurringCommitment(
        household_id=account.household_id, name="Council Tax",
        account_id=account.id,
        amount_pence=kw.pop("amount_pence", -15000),
        frequency=kw.pop("frequency", Frequency.monthly),
        next_date=kw.pop("next_date", date(2026, 1, 31)),
        **kw,
    )


class TestAdvance:
    @pytest.mark.parametrize("freq,start,expect", [
        ("weekly", "2026-01-31", "2026-02-07"),
        ("fortnightly", "2026-01-31", "2026-02-14"),
        ("monthly", "2026-01-31", "2026-02-28"),   # clamps
        ("monthly", "2024-01-31", "2024-02-29"),   # leap
        ("quarterly", "2026-11-30", "2027-02-28"), # wraps year + clamps
        ("annually", "2024-02-29", "2025-02-28"),  # 29 Feb -> 28
        ("annually", "2026-06-15", "2027-06-15"),
    ])
    def test_matrix(self, freq: str, start: str, expect: str) -> None:
        s = date.fromisoformat(start)
        assert advance(s, Frequency(freq), None).isoformat() == expect


class TestOccurrences:
    def test_end_date_cutoff(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account, end_date=date(2026, 3, 31))
        assert list(occurrences(c, date(2026, 6, 30))) == [
            date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]

    def test_occurrence_count(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account, occurrence_count=2)
        assert len(list(occurrences(c, date(2027, 1, 1)))) == 2

    def test_open_ended(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account)
        assert len(list(occurrences(c, date(2026, 4, 30)))) == 4


class TestMaterialise:
    def test_creates_pending_rows(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account)
        session.add(c)
        session.flush()
        made = materialise(session, c, date(2026, 3, 31))
        session.commit()
        assert len(made) == 3
        assert all(t.status.value == "pending" for t in made)
        assert all(t.amount_pence == -15000 for t in made)
        assert c.generated_count == 3
        assert c.next_date == date(2026, 4, 30)  # 31st clamps

    def test_posting_partial_and_prepaid_occurrences_consumes_only_the_remainder(
        self, session: Session, account: Account,
    ) -> None:
        bill = _rc(account, occurrence_count=2)
        session.add(bill)
        session.flush()
        schedule.rematerialize_commitment(session, bill, today=bill.next_date)
        movements = list(session.scalars(select(ExpectedMovement).order_by(
            ExpectedMovement.expected_date)))
        for movement, paid in zip(movements, [-6000, -15000], strict=True):
            tx = Transaction(account_id=account.id, amount_pence=paid, payee="Payment",
                             date=bill.next_date, status="cleared")
            session.add(tx)
            session.flush()
            settle.settle_occurrence(session, movement, tx, manual=True)
        assert bill.next_date == date(2026, 1, 31)
        posted = materialise(session, bill, date(2026, 2, 28))
        assert [(t.date, t.amount_pence) for t in posted] == [(date(2026, 1, 31), -9000)]
        assert [contributions.status(session, m).outstanding_pence for m in movements] == [0, 0]
        assert bill.generated_count == 2
        assert materialise(session, bill, date(2026, 2, 28)) == []

    @pytest.mark.parametrize("state", ["unpaid", "partial", "written_off"])
    def test_rescheduled_occurrence_honours_its_new_date_and_outstanding_amount(
        self, session: Session, account: Account, state: str,
    ) -> None:
        bill = _rc(account, occurrence_count=1)
        session.add(bill)
        session.flush()
        schedule.rematerialize_commitment(session, bill, today=bill.next_date)
        movement = session.scalars(select(ExpectedMovement)).one()
        if state == "partial":
            tx = Transaction(account_id=account.id, amount_pence=-6000, payee="Partial payment",
                             date=bill.next_date, status="cleared")
            session.add(tx)
            session.flush()
            settle.settle_occurrence(session, movement, tx, manual=True)
        for due in [date(2026, 2, 2), date(2026, 2, 4)]:
            contributions.reschedule(session, movement, due)
        if state == "written_off":
            contributions.write_off(session, movement, when=datetime.now(UTC))
        schedule.rematerialize_commitment(session, bill, today=date(2026, 1, 30))
        assert list(session.scalars(select(ExpectedMovement))) == [movement]
        assert materialise(session, bill, date(2026, 1, 31)) == []
        schedule.rematerialize_commitment(session, bill, today=date(2026, 1, 31))
        posted = materialise(session, bill, date(2026, 2, 4))
        if state == "written_off":
            assert posted == []
        else:
            remaining = -9000 if state == "partial" else -15000
            assert [(t.date, t.amount_pence) for t in posted] == [(date(2026, 2, 4), remaining)]
            assert contributions.status(session, movement).outstanding_pence == 0
        assert bill.generated_count == 1
        assert materialise(session, bill, date(2026, 2, 4)) == []

    @pytest.fixture
    def posted_remainder(self, session, account):
        bill = _rc(account, occurrence_count=1)
        session.add(bill)
        session.flush()
        schedule.rematerialize_commitment(session, bill, today=bill.next_date)
        movement = session.scalars(select(ExpectedMovement)).one()
        initial = Transaction(account_id=account.id, date=bill.next_date, amount_pence=-6000,
                              payee=bill.name, source="starling", status="cleared")
        session.add(initial)
        session.flush()
        # The first receipt has its own matched reconciliation snapshot.
        assert reconcile.reconcile_account(session, account, initial.date).queued == 1
        reconcile.confirm(session, session.scalars(select(Reconciliation)).one())
        pending, = materialise(session, bill, bill.next_date)
        receipt = Transaction(account_id=account.id, date=date(2026, 2, 1),
                              payee=bill.name,
                              amount_pence=-9000, source="starling", status="cleared",
                              external_ref="test-bank-receipt")
        session.add(receipt)
        session.flush()
        return bill, movement, pending, receipt

    @pytest.mark.parametrize("route", ["auto", "review", "manual"])
    def test_bank_evidence_replaces_posting(self, session, account, posted_remainder, route):
        _, movement, pending, receipt = posted_remainder
        if route == "manual":
            with pytest.raises(contributions.MatchError):
                contributions.confirm(session, movement, receipt, pence=9500)
            assert pending.status == "pending"
            contributions.confirm(session, movement, receipt)
        else:
            if route == "review":
                receipt.date, receipt.payee = date(2026, 2, 5), "Bank description"
            stats = reconcile.reconcile_account(session, account, receipt.date)
            if route == "review":
                assert stats.queued == 1 and pending.status == "pending"
                queued = session.scalars(select(Reconciliation).where(
                    Reconciliation.transaction_id == receipt.id)).one()
                reconcile.confirm(session, queued)
            else:
                assert stats.matched == 1
            assert reconcile.reconcile_account(session, account, receipt.date).matched == 0
        assert pending.status == "void"
        assert contributions.status(session, movement).outstanding_pence == 0

    @pytest.mark.parametrize("merge_status", ["merged", "pending"])
    def test_existing_bank_merge_protects_posting(self, session, account,
                                                 posted_remainder, merge_status):
        _, movement, pending, receipt = posted_remainder
        session.add(MergeCandidate(household_id=account.household_id, account_id=account.id,
                    existing_txn_id=pending.id, incoming={"external_ref": "test-bank-ref"},
                    status=merge_status))
        session.flush()
        with pytest.raises(contributions.MatchError):
            contributions.confirm(session, movement, receipt)
        assert reconcile.reconcile_account(session, account, receipt.date).matched == 0
        assert pending.status == "pending"

    @pytest.mark.parametrize("smaller_receipt", [True, False])
    def test_split_replacement_retains_pending_amount(self, session, account,
                                                       posted_remainder, smaller_receipt):
        bill, movement, pending, receipt = posted_remainder
        if smaller_receipt:
            receipt.amount_pence = -4000
            assert reconcile.reconcile_account(session, account, receipt.date).queued == 1
            queued = session.scalars(select(Reconciliation).where(
                Reconciliation.transaction_id == receipt.id)).one()
            reconcile.confirm(session, queued)
        else:
            contributions.confirm(session, movement, receipt, pence=4000)
        assert pending.status == "pending" and pending.amount_pence == -5000
        assert materialise(session, bill, bill.next_date) == []
        assert contributions.status(session, movement).outstanding_pence == 0
        final = Transaction(account_id=account.id, date=receipt.date, payee=bill.name,
                            amount_pence=-5000, source="starling", status="cleared")
        session.add(final)
        session.flush()
        assert reconcile.reconcile_account(session, account, final.date).matched == 1
        assert pending.status == "void"
        assert contributions.status(session, movement).outstanding_pence == 0
        assert sum(t.amount_pence for t in session.scalars(select(Transaction)).all()
                   if t.status != "void") == (-15000 if smaller_receipt else -20000)

    @pytest.mark.parametrize("route", ["manual_void", "bank_void", "partial_void", "user_void"])
    def test_retry_after_evidence_is_void(self, session, account, posted_remainder, route):
        from app.domain import periods, sync
        from app.persistence.models import SyncRun, TransactionSource, TransactionStatus

        bill, movement, pending, receipt = posted_remainder
        partial = route in ("partial_void", "user_void")
        contributions.confirm(session, movement, receipt, pence=4000 if partial else None)
        if route == "user_void":
            periods.transition(session, pending, TransactionStatus.void)
        if route == "bank_void":
            item = sync.FeedItem(receipt.external_ref, receipt.date, receipt.amount_pence,
                                 receipt.payee, status=TransactionStatus.void)
            sync._ingest_item(session, account, TransactionSource.starling, item,
                              SyncRun(fetched=0, deduped=0))
        else:
            periods.transition(session, receipt, TransactionStatus.void)
        assert receipt.status == "void"
        generated = materialise(session, bill, bill.next_date)
        if route == "user_void":
            assert generated == [] and pending.status == "void"
            assert contributions.status(session, movement).outstanding_pence == 9000
            return
        restored = pending if partial else generated[0]
        assert restored.status == "pending" and restored.amount_pence == -9000
        assert contributions.status(session, movement).outstanding_pence == 0
        assert sum(t.amount_pence for t in session.scalars(select(Transaction)).all()
                   if t.status != "void") == -15000
        assert materialise(session, bill, bill.next_date) == []

    def test_replacement_with_same_ids_has_its_own_posting_date(
        self, session: Session, account: Account,
    ) -> None:
        from app.api.audit import emit

        old = _rc(account, occurrence_count=1)
        session.add(old)
        session.flush()
        schedule.rematerialize_commitment(session, old, today=old.next_date)
        movement = session.scalars(select(ExpectedMovement)).one()
        identifiers = old.id, movement.id
        new_due = date(2026, 2, 4)
        contributions.reschedule(session, movement, new_due)
        emit(session, account.household_id, "test", "reschedule", "expected_movement",
             movement.id, {"from": str(old.next_date), "to": str(new_due)})
        session.delete(movement)
        session.delete(old)
        session.flush()
        replacement = _rc(account, next_date=new_due, occurrence_count=1)
        session.add(replacement)
        session.flush()
        schedule.rematerialize_commitment(session, replacement, today=new_due)
        fresh = session.scalars(select(ExpectedMovement)).one()
        assert (replacement.id, fresh.id) == identifiers
        assert fresh.original_expected_date is None
        assert rescheduled_occurrences(session, replacement) == {}
        assert [(t.date, t.amount_pence) for t in materialise(session, replacement, new_due)] == [
            (new_due, -15000)]

    @pytest.mark.parametrize("partially_paid", [False, True])
    def test_written_off_occurrence_advances_without_posting(
        self, session: Session, account: Account, partially_paid: bool,
    ) -> None:
        bill = _rc(account, occurrence_count=1)
        session.add(bill)
        session.flush()
        schedule.rematerialize_commitment(session, bill, today=bill.next_date)
        movement = session.scalars(select(ExpectedMovement)).one()
        if partially_paid:
            tx = Transaction(account_id=account.id, amount_pence=-6000,
                             payee="Partial payment", date=bill.next_date, status="cleared")
            session.add(tx)
            session.flush()
            settle.settle_occurrence(session, movement, tx, manual=True)
        contributions.write_off(session, movement, when=datetime.now(UTC))
        assert materialise(session, bill, bill.next_date) == []
        assert bill.generated_count == 1
        assert bill.next_date == date(2026, 2, 28)

    def test_idempotent_rerun(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account)
        session.add(c)
        session.flush()
        materialise(session, c, date(2026, 3, 31))
        session.commit()
        again = materialise(session, c, date(2026, 3, 31))
        assert again == []
        # but advancing the horizon produces only the new row
        more = materialise(session, c, date(2026, 4, 30))
        assert len(more) == 1

    def test_inactive_produces_nothing(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account, active=False)
        session.add(c)
        session.flush()
        assert materialise(session, c, date(2026, 6, 30)) == []

    def test_formula_commitment_refused(
        self, session: Session, account: Account
    ) -> None:
        c = _rc(account, amount_pence=None, formula="payroll.net")
        session.add(c)
        session.flush()
        with pytest.raises(ValueError, match="payroll"):
            materialise(session, c, date(2026, 3, 31))


class TestConstraints:
    @pytest.mark.parametrize("kw", [
        {"amount_pence": -100, "formula": "x"},          # both set
        {"amount_pence": None, "formula": None},         # neither
    ])
    def test_amount_xor_formula(
        self, session: Session, account: Account, kw: dict
    ) -> None:
        session.add(_rc(account, **kw))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_day_of_month_bounds(
        self, session: Session, account: Account
    ) -> None:
        session.add(_rc(account, day_of_month=32))
        with pytest.raises(IntegrityError):
            session.flush()
