"""Canonical banking transitions; synthetic records only (#522)."""
from datetime import date

import pytest
from sqlalchemy import select

from app.domain import (
    banks,
    categorise,
    discovery,
    outbox,
    periods,
    reconcile,
    refunds,
    sync,
    transfers,
)
from app.persistence.models import (
    Account,
    ConnStatus,
    IntegrationEvent,
    Transaction,
    TransactionSource,
    TransactionStatus,
)
from tests.test_banking_live142 import linked


def rows(session, kind):
    return list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == kind)))


def transaction(session, account, amount=-500, status=TransactionStatus.pending, payee="Synthetic"):
    row = Transaction(account_id=account.id, date=date.today(), amount_pence=amount,
                      payee=payee, status=status, source=TransactionSource.manual)
    session.add(row)
    session.flush()
    return row


def test_provider_settlement_emits_once_on_insert_and_pending_transition(session, household):
    account, _, _ = linked(session, household)
    pending = sync.FeedItem("pending", date.today(), -500, "Synthetic", TransactionStatus.pending)
    cleared = sync.FeedItem("pending", date.today(), -500, "Synthetic", TransactionStatus.cleared)
    for item in (pending, cleared, cleared):
        sync.sync_account(session, account, TransactionSource.starling,
                          lambda _, item=item: iter([sync.FeedPage([item])]))
    events = rows(session, "transaction.settled")
    assert len(events) == 1
    assert events[0].entity_kind == "transaction"
    assert events[0].entity_id == session.scalar(select(Transaction.id))
    assert "payee" not in outbox.visible_payload(events[0], False)
    assert outbox.visible_payload(events[0], True)["payee"] == "Synthetic"


def test_manual_clearing_is_transactional(session, household):
    account, _, _ = linked(session, household)
    txn = transaction(session, account)
    session.commit()
    periods.transition(session, txn, TransactionStatus.cleared)
    assert len(rows(session, "transaction.settled")) == 1
    session.rollback()
    assert rows(session, "transaction.settled") == []
    assert txn.status == TransactionStatus.pending


def test_refund_link_is_authoritative_and_rejected_replay_emits_nothing(session, household):
    account, _, _ = linked(session, household)
    original = transaction(session, account)
    refund = transaction(session, account, 200)
    refunds.link_refund(session, refund, original)
    assert len(rows(session, "transaction.refund_linked")) == 1
    with pytest.raises(refunds.RefundError):
        refunds.link_refund(session, refund, original)
    assert len(rows(session, "transaction.refund_linked")) == 1


@pytest.mark.parametrize("automatic", [False, True])
def test_transfer_events_are_scoped_per_leg_without_counterparty_leak(
        session, household, automatic):
    from app.persistence.models import UserProfile
    owner = UserProfile(household_id=household.id, display_name="Synthetic owner")
    session.add(owner)
    session.flush()
    debit_account, _, _ = linked(session, household)
    credit_account = Account(household_id=household.id, name="Synthetic private", type="personal",
                             opening_date=date.today(), opening_balance_pence=0,
                             owner_profile_id=owner.id)
    session.add(credit_account)
    session.flush()
    debit = transaction(session, debit_account, -500, payee=credit_account.name)
    credit = transaction(session, credit_account, 500, payee=debit_account.name)
    if automatic:
        assert categorise.auto_pair_transfers(session, household.id) == 1
        assert categorise.auto_pair_transfers(session, household.id) == 0
    else:
        transfers.create_transfer(session, household.id, debit, credit)
    events = rows(session, "transaction.transfer_linked")
    assert {event.entity_id for event in events} == {debit.id, credit.id}
    # Even detailed events must not copy the other leg's account identity or payee.
    assert all("counterparty_account_id" not in event.payload for event in events)
    visible = [event.entity_id for event in events if outbox.event_visible(session, event, None)]
    assert visible == [debit.id]
    assert all(outbox.event_visible(session, event, owner.id) for event in events)


def test_balance_changes_preserve_repeated_values_and_cycles(session, household):
    account, conn, mapping = linked(session, household)
    class Client:
        value = 1000
        def balance(self, *args):
            return self.value
    client = Client()
    for value in (1000, 1000, 2000, 2000, 1000):
        client.value = value
        discovery.sync_balances(session, conn, client, "synthetic")
    events = rows(session, "account.balance_changed")
    assert len(events) == 2
    assert [event.payload["balance_pence"] for event in events] == [2000, 1000]
    assert all(event.entity_id == account.id for event in events)
    assert all("balance_pence" not in outbox.visible_payload(event, False) for event in events)
    assert mapping.balance_pence == 1000


def test_only_explicit_connection_revocation_emits_sanitised_transition(session, household):
    _, conn, _ = linked(session, household)
    banks.record_api_error(session, conn, 500, "must not export provider error")
    assert rows(session, "connection.revoked") == []
    banks.record_api_error(session, conn, 401, "must not export provider error")
    banks.record_api_error(session, conn, 401, "must not export provider error")
    events = rows(session, "connection.revoked")
    assert len(events) == 1
    assert "must not export" not in str(events[0].payload)
    assert conn.status == ConnStatus.revoked


def test_review_queue_and_manual_confirmation_emit_distinct_canonical_events(session, household):
    from app.persistence.models import Reconciliation
    from tests.test_reconcile import _expected

    account, _, _ = linked(session, household)
    transaction(session, account)
    _expected(session, household, account, date.today(), -500, "One", source_id=1)
    _expected(session, household, account, date.today(), -500, "Two", source_id=2)
    assert reconcile.reconcile_account(session, account, date.today()).queued == 1
    assert reconcile.reconcile_account(session, account, date.today()).queued == 0
    assert len(rows(session, "transaction.review_required")) == 1
    assert rows(session, "transaction.reconciled") == []
    proposal = session.scalar(select(Reconciliation))
    reconcile.confirm(session, proposal)
    assert len(rows(session, "transaction.reconciled")) == 1
    with pytest.raises(reconcile.ReconError):
        reconcile.confirm(session, proposal)
    assert len(rows(session, "transaction.reconciled")) == 1


@pytest.mark.parametrize("protected", [TransactionStatus.reconciled, TransactionStatus.void])
def test_provider_replay_preserves_terminal_local_state_without_new_settlement(
        session, household, protected):
    account, _, _ = linked(session, household)
    item = sync.FeedItem("stable", date.today(), -500, "Synthetic", TransactionStatus.cleared)
    def fetch(_):
        return iter([sync.FeedPage([item])])
    sync.sync_account(session, account, TransactionSource.starling, fetch)
    txn = session.scalar(select(Transaction))
    txn.status = protected
    session.commit()
    sync.sync_account(session, account, TransactionSource.starling, fetch)
    assert txn.status == protected
    assert len(rows(session, "transaction.settled")) == 1


@pytest.mark.parametrize("late_status", [None, 401, 403, 429, 500])
def test_disconnect_and_unmapped_connection_visibility(session, household, late_status):
    from app.domain import vault
    from app.persistence.models import BankAccountMapping
    _, conn, mapping = linked(session, household)
    session.delete(mapping)
    session.flush()
    banks.disconnect(session, conn, vault.Keyring({1: b"0" * 32}))
    banks.record_api_error(session, conn, late_status, "late provider response")
    assert conn.status == ConnStatus.revoked
    banks.disconnect(session, conn, vault.Keyring({1: b"0" * 32}))
    event, = rows(session, "connection.disconnected")
    assert rows(session, "connection.revoked") == []
    assert not outbox.event_visible(session, event, None)
    assert outbox.event_visible(session, event, None, all_scopes=True)
    assert session.scalar(select(BankAccountMapping)) is None


def test_automatic_reconciliation_is_distinct_from_provider_settlement(session, household):
    from tests.test_reconcile import _expected
    account, _, _ = linked(session, household)
    transaction(session, account, status=TransactionStatus.cleared)
    _expected(session, household, account, date.today(), -500, "Synthetic")
    assert reconcile.reconcile_account(session, account, date.today()).matched == 1
    assert reconcile.reconcile_account(session, account, date.today()).matched == 0
    assert len(rows(session, "transaction.reconciled")) == 1
    assert rows(session, "transaction.review_required") == []


@pytest.mark.parametrize("writer,event_type", [
    ("refund", "transaction.refund_linked"),
    ("transfer", "transaction.transfer_linked"),
    ("balance", "account.balance_changed"),
    ("connection", "connection.revoked"),
])
def test_banking_writer_rollback_removes_its_events(session, household, writer, event_type):
    account, conn, mapping = linked(session, household)
    other = Account(household_id=household.id, name="Second", type="personal",
                    opening_date=date.today(), opening_balance_pence=0)
    session.add(other)
    session.flush()
    debit = transaction(session, account, -500)
    credit = transaction(session, other, 500)
    mapping.balance_pence = 1000
    session.commit()
    if writer == "refund":
        refunds.link_refund(session, credit, debit)
    elif writer == "transfer":
        transfers.create_transfer(session, household.id, debit, credit)
    elif writer == "balance":
        class Client:
            def balance(self, *args):
                return 2000
        discovery.sync_balances(session, conn, Client(), "synthetic")
    else:
        banks.record_api_error(session, conn, 401)
    assert rows(session, event_type)
    session.rollback()
    assert rows(session, event_type) == []


def test_provider_overlap_queues_one_review_across_replay(session, household):
    from app.persistence.models import MergeCandidate
    account, _, _ = linked(session, household)
    original = transaction(session, account, status=TransactionStatus.cleared)
    item = sync.FeedItem("overlap", date.today(), -500, "Synthetic", TransactionStatus.cleared)
    for _ in range(2):
        sync.sync_account(session, account, TransactionSource.starling,
                          lambda _: iter([sync.FeedPage([item])]))
    event, = rows(session, "transaction.review_required")
    assert event.entity_id == original.id
    assert len(list(session.scalars(select(MergeCandidate)))) == 1
    assert event.payload["status"] == "pending_review"


def test_existing_duplicate_overlap_candidates_do_not_abort_provider_replay(session, household):
    from app.persistence.models import MergeCandidate
    account, _, _ = linked(session, household)
    original = transaction(session, account, status=TransactionStatus.cleared)
    for _ in range(2):
        session.add(MergeCandidate(
            household_id=household.id, account_id=account.id, existing_txn_id=original.id,
            incoming={"external_ref": "legacy-overlap", "date": date.today().isoformat(),
                      "amount_pence": -500, "payee": "Synthetic"}))
    session.flush()
    item = sync.FeedItem("legacy-overlap", date.today(), -500, "Synthetic",
                         TransactionStatus.cleared)
    run = sync.sync_account(session, account, TransactionSource.starling,
                            lambda _: iter([sync.FeedPage([item])]))
    assert run.deduped == 1
    assert len(list(session.scalars(select(MergeCandidate)))) == 2
    assert rows(session, "transaction.review_required") == []


def test_import_without_explicit_provider_settlement_does_not_claim_settlement(session, household):
    account, _, _ = linked(session, household)
    item = sync.FeedItem("unknown-status", date.today(), -500, "Synthetic")
    sync.sync_account(session, account, TransactionSource.starling,
                      lambda _: iter([sync.FeedPage([item])]))
    assert len(rows(session, "transaction.imported")) == 1
    assert rows(session, "transaction.settled") == []


def test_stale_provider_session_cannot_revive_removed_credentials(session, household):
    from sqlalchemy.orm import Session

    from app.domain import vault
    _, conn, _ = linked(session, household)
    ring = vault.Keyring({1: b"0" * 32})
    vault.store(session, household.id, conn.vault_ref, b"synthetic", ring)
    session.commit()
    assert conn.status == ConnStatus.connected  # cache the in-flight request's state
    with Session(session.get_bind()) as other:
        banks.disconnect(other, other.get(type(conn), conn.id), ring)
        other.commit()
    assert conn.status == ConnStatus.connected
    banks.record_api_error(session, conn, 500, "late provider response")
    assert conn.status == ConnStatus.revoked
    session.commit()
    assert rows(session, "connection.revoked") == []
    assert len(rows(session, "connection.disconnected")) == 1


@pytest.mark.parametrize("writer", ["transaction", "imported", "balance", "connection"])
def test_original_private_banking_scope_survives_account_becoming_shared(
        session, household, writer):
    from app.persistence.models import UserProfile
    owner = UserProfile(household_id=household.id, display_name="Original owner")
    session.add(owner)
    session.flush()
    account, conn, mapping = linked(session, household)
    account.owner_profile_id = owner.id
    if writer == "transaction":
        txn = transaction(session, account)
        periods.transition(session, txn, TransactionStatus.cleared)
        event, = rows(session, "transaction.settled")
    elif writer == "imported":
        item = sync.FeedItem("private-import", date.today(), -500, "Synthetic",
                             TransactionStatus.pending)
        sync.sync_account(session, account, TransactionSource.starling,
                          lambda _: iter([sync.FeedPage([item])]))
        event, = rows(session, "transaction.imported")
    elif writer == "balance":
        class Client:
            def balance(self, *args):
                return 2000
        mapping.balance_pence = 1000
        discovery.sync_balances(session, conn, Client(), "synthetic")
        event, = rows(session, "account.balance_changed")
    else:
        banks.record_api_error(session, conn, 401)
        event, = rows(session, "connection.revoked")
    assert not outbox.event_visible(session, event, None)
    account.owner_profile_id = None
    session.flush()
    assert not outbox.event_visible(session, event, None)
    assert outbox.event_visible(session, event, owner.id)
    assert outbox.event_visible(session, event, None, all_scopes=True)
    assert "_scope_owner_profile_id" not in outbox.visible_payload(event, True)
    assert "_scope_owner_profile_id" not in outbox.visible_payload(event, False)
