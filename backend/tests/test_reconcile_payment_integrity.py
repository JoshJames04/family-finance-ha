"""A reconciliation must describe actual eligible payment evidence, not a no-op."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import contributions, reconcile, settle, transactions
from app.main import create_app
from app.persistence.models import (
    Account,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Household,
    Reconciliation,
    ReconStatus,
    Transaction,
    TransactionStatus,
    UserProfile,
)

DAY = date(2025, 4, 10)


@pytest.fixture()
def account(session, household):
    row = Account(household_id=household.id, name="Synthetic", type="joint", opening_date=DAY)
    session.add(row)
    session.flush()
    return row


def movement(session, account, source_id=1, label="Synthetic", on=DAY, amount=1000):
    row = ExpectedMovement(household_id=account.household_id, account_id=account.id,
        expected_date=on, amount_pence=amount, label=label, confidence="scheduled",
        source_type=ExpectedSource.manual, source_id=source_id)
    session.add(row)
    session.flush()
    return row


def payment(session, account, amount=1000, payee="Synthetic", status=TransactionStatus.cleared):
    row = Transaction(account_id=account.id, date=DAY, amount_pence=amount,
                      payee=payee, status=status, source="manual")
    session.add(row)
    session.flush()
    return row


def pending(session, expected, txn, household_id=None):
    row = Reconciliation(household_id=household_id or expected.household_id,
        account_id=txn.account_id, transaction_id=txn.id, status=ReconStatus.pending,
        score=60, expected_source_type=expected.source_type.value,
        expected_source_id=expected.source_id, expected_label=expected.label,
        expected_date=expected.expected_date, expected_amount_pence=expected.amount_pence)
    session.add(row)
    session.flush()
    return row


def test_corrected_payment_allocated_to_a_cannot_be_reconciled_as_b(session, account):
    first = movement(session, account, label="Different first expectation")
    second = movement(session, account, 2, "Exact new label", DAY + timedelta(days=1))
    original = payment(session, account, status=TransactionStatus.reconciled)
    contributions.confirm(session, first, original)
    corrected = transactions.correct(session, original, "Synthetic", payee="Exact new label")
    result = reconcile.reconcile_account(session, account, DAY)
    assert (result.matched, result.queued) == (0, 0)
    assert corrected.status == TransactionStatus.pending
    assert session.scalar(select(func.count()).select_from(Reconciliation)) == 0
    assert contributions.matched_total(session, first.id) == 1000
    assert contributions.matched_total(session, second.id) == 0


@pytest.mark.parametrize("alternative", [False, True])
def test_paid_expectation_cannot_claim_another_receipt_or_hide_lower_candidate(
        session, account, alternative):
    first = movement(session, account)
    paid = payment(session, account, status=TransactionStatus.reconciled)
    contributions.confirm(session, first, paid)
    second = (movement(session, account, 2, "Other", DAY + timedelta(days=1))
              if alternative else None)
    fresh = payment(session, account)
    result = reconcile.reconcile_account(session, account, DAY)
    assert result.matched == (1 if alternative else 0)
    assert fresh.status == (TransactionStatus.reconciled if alternative
                            else TransactionStatus.cleared)
    rows = list(session.scalars(select(Reconciliation)))
    assert [row.expected_source_id for row in rows] == ([second.source_id] if second else [])
    if second:
        assert contributions.matched_total(session, second.id) == 1000


def test_foreign_candidate_does_not_abort_later_valid_receipt(session, account):
    foreign = Household(name="Synthetic foreign")
    session.add(foreign)
    session.flush()
    wrong = movement(session, account, label="Foreign")
    wrong.household_id = foreign.id
    valid = movement(session, account, 2, "Valid", amount=2500)
    unmatched = payment(session, account, payee="Foreign")
    received = payment(session, account, 2500, "Valid")
    result = reconcile.reconcile_account(session, account, DAY)
    assert result.matched == 1
    assert unmatched.status == TransactionStatus.cleared
    assert received.status == TransactionStatus.reconciled
    assert contributions.matched_total(session, wrong.id) == 0
    assert contributions.matched_total(session, valid.id) == 2500


def test_settlement_noop_does_not_write_false_reconciliation(session, account, monkeypatch):
    expected = movement(session, account)
    other = payment(session, account, status=TransactionStatus.reconciled)
    fresh = payment(session, account)
    original = settle.settle_occurrence

    def settle_after_other_receipt(session, expected, txn, *, manual):
        contributions.confirm(session, expected, other)
        return original(session, expected, txn, manual=manual)

    monkeypatch.setattr(settle, "settle_occurrence", settle_after_other_receipt)
    result = reconcile.reconcile_account(session, account, DAY)
    assert (result.matched, result.queued) == (0, 0)
    assert fresh.status == TransactionStatus.cleared
    assert session.scalar(select(func.count()).select_from(Reconciliation)) == 0
    assert contributions.matched_total(session, expected.id) == 1000


@pytest.mark.parametrize("stale", ["allocated_elsewhere", "target_paid", "transaction_scope",
                                    "foreign_target", "dismissed_target"])
def test_stale_confirmation_rejects_before_status_writes(session, account, stale):
    expected = movement(session, account)
    txn = payment(session, account)
    row = pending(session, expected, txn)
    if stale == "allocated_elsewhere":
        other = movement(session, account, 2)
        contributions.confirm(session, other, txn)
    elif stale == "target_paid":
        other_txn = payment(session, account, status=TransactionStatus.reconciled)
        contributions.confirm(session, expected, other_txn)
    elif stale in {"transaction_scope", "foreign_target"}:
        foreign = Household(name="Synthetic foreign")
        session.add(foreign)
        session.flush()
        if stale == "transaction_scope":
            foreign_account = Account(household_id=foreign.id, name="Foreign", type="joint",
                                      opening_date=DAY)
            session.add(foreign_account)
            session.flush()
            txn.account_id = foreign_account.id
        else:
            expected.household_id = foreign.id
    else:
        from datetime import datetime
        expected.dismissed_at = datetime(2025, 4, 10)
    session.flush()
    with pytest.raises(contributions.MatchError):
        reconcile.confirm(session, row)
    assert row.status == ReconStatus.pending and row.resolved_at is None
    assert txn.status == TransactionStatus.cleared
    assert not session.dirty and not session.new


def test_same_target_existing_evidence_can_confirm_without_duplicate_match(session, account):
    expected = movement(session, account)
    txn = payment(session, account)
    row = pending(session, expected, txn)
    contributions.confirm(session, expected, txn)
    reconcile.confirm(session, row)
    assert row.status == ReconStatus.matched and txn.status == TransactionStatus.reconciled
    assert contributions.matched_total(session, expected.id) == 1000
    assert session.scalar(select(func.count()).select_from(ExpectedMatch)) == 1


@pytest.mark.parametrize("rematerialize", [False, True])
def test_valid_pending_snapshot_survives_rematerialization_or_absent_live_target(
        session, account, rematerialize):
    expected = movement(session, account)
    txn = payment(session, account)
    row = pending(session, expected, txn)
    session.delete(expected)
    session.flush()
    if rematerialize:
        movement(session, account)
    reconcile.confirm(session, row)
    assert row.status == ReconStatus.matched and txn.status == TransactionStatus.reconciled
    assert session.scalar(select(func.count()).select_from(ExpectedMatch)) == int(rematerialize)


def test_stale_payment_confirmation_api_is_409_and_preserves_pending_row(tmp_path):
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"})
    with TestClient(create_app(settings)) as client:
        with Session(client.app.state.engine) as session:
            household = Household(name="Synthetic")
            session.add(household)
            session.flush()
            owner = UserProfile(household_id=household.id, display_name="Synthetic", role="admin")
            session.add(owner)
            session.flush()
            account = Account(household_id=household.id, name="Synthetic", type="joint",
                              opening_date=DAY)
            session.add(account)
            session.flush()
            target = movement(session, account)
            other = movement(session, account, 2)
            txn = payment(session, account)
            row = pending(session, target, txn)
            contributions.confirm(session, other, txn)
            session.commit()
            rid, tid, hid, pid = row.id, txn.id, household.id, owner.id
        response = client.post(f"/api/v1/reconcile/{rid}/confirm", headers={
            "X-Household-Id": str(hid), "X-Profile-Id": str(pid)})
        assert response.status_code == 409, response.text
        with Session(client.app.state.engine) as session:
            assert session.get(Reconciliation, rid).status == ReconStatus.pending
            assert session.get(Transaction, tid).status == TransactionStatus.cleared
            assert session.scalar(select(func.count()).select_from(ExpectedMatch)) == 1


@pytest.mark.parametrize("runner", ["account", "post_sync"])
@pytest.mark.parametrize("shape", ["self", "corrected_alias"])
def test_automatic_reconcile_skips_cyclic_high_score_and_settles_valid_payment(
        session, account, runner, shape):
    corrupt_target = movement(session, account)
    corrupt = payment(session, account)
    if shape == "self":
        corrupt.corrects_id = corrupt.id
        corrupt_rows = [corrupt]
    else:
        corrupt.status = TransactionStatus.reconciled
        current = transactions.correct(session, corrupt, "Synthetic cyclic legacy")
        corrupt.corrects_id = current.id
        corrupt_rows = [corrupt, current]
    valid_target = movement(session, account, 2, "Unrelated", amount=2500)
    valid = payment(session, account, 2500, "Unrelated")
    session.flush()
    before = [(row.id, row.corrects_id, row.status, row.category_id, row.amount_pence)
              for row in corrupt_rows]
    result = (reconcile.reconcile_account(session, account, DAY) if runner == "account"
              else settle.post_sync(session, account.household_id, today=DAY))
    assert result.matched == 1 and result.queued == 0
    assert valid.status == TransactionStatus.reconciled
    assert contributions.matched_total(session, valid_target.id) == 2500
    assert contributions.matched_total(session, corrupt_target.id) == 0
    assert [(row.id, row.corrects_id, row.status, row.category_id, row.amount_pence)
            for row in corrupt_rows] == before
    assert session.scalar(select(func.count()).select_from(Reconciliation).where(
        Reconciliation.transaction_id.in_([row.id for row in corrupt_rows]))) == 0
    assert session.scalar(select(func.count()).select_from(ExpectedMatch).where(
        ExpectedMatch.transaction_id.in_([row.id for row in corrupt_rows]))) == 0
