"""Committed bill/subscription lifecycle, with replay and privacy regressions (#521)."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api import schemas
from app.api.v1 import commitments, integration, subscriptions
from app.domain import financial_events, outbox, recurring, settle
from app.domain import subscriptions as subs
from app.domain.money import Money
from app.persistence.models import Account, Frequency, IntegrationEvent, Role, UserProfile

DAY = date(2030, 1, 15)


@pytest.fixture()
def account(session, household):
    row = Account(household_id=household.id, name="Shared", type="personal",
                  opening_balance_pence=0, opening_date=DAY)
    session.add(row)
    session.flush()
    return row


def bill(session, household, account, **kw):
    return commitments.create_commitment(schemas.CommitmentIn(
        name="Synthetic bill", account_id=account.id, amount=Money.of_pence(-1000),
        frequency=Frequency.monthly, next_date=DAY, notes="NEVER ON BUS", **kw),
        household, session, "test")


def subscription(session, household, account):
    return subs.create(session, household.id, payee="Synthetic subscription",
                       frequency=Frequency.monthly, expected_pence=1000,
                       next_renewal_date=DAY, account_id=account.id, notes="NEVER ON BUS")


def types(session):
    return list(session.scalars(select(IntegrationEvent.event_type).order_by(IntegrationEvent.id)))


def test_bill_create_edit_pause_resume_noops(session, household, account):
    row = bill(session, household, account)
    commitments.update_commitment(row.id, schemas.CommitmentPatch(name="Renamed", notes="SECRET"),
                                  household, session, "test")
    commitments.update_commitment(row.id, schemas.CommitmentPatch(amount=Money.of_pence(-1200),
                                  next_date=date(2030, 1, 20)), household, session, "test")
    commitments.update_commitment(row.id, schemas.CommitmentPatch(amount=Money.of_pence(-1200),
                                  next_date=date(2030, 1, 20)), household, session, "test")
    commitments.deactivate_commitment(row.id, household, session, "test", permanent=False)
    commitments.deactivate_commitment(row.id, household, session, "test", permanent=False)
    commitments.activate_commitment(row.id, household, session, "test")
    commitments.activate_commitment(row.id, household, session, "test")
    assert types(session) == ["bill.created", "bill.amount_changed", "bill.date_changed",
                              "bill.paused", "bill.resumed"]
    events = list(session.scalars(select(IntegrationEvent)))
    assert {e.entity_id for e in events} == {row.id}
    assert {e.entity_kind for e in events} == {"commitment"}
    for e in events:
        assert "SECRET" not in str(e.payload) and "NEVER ON BUS" not in str(e.payload)
        safe = outbox.visible_payload(e, False)
        assert "name" not in safe and "amount_pence" not in safe
    assert events[1].payload["name"] == "Renamed"


@pytest.mark.parametrize("ending", [{"occurrence_count": 1}, {"end_date": DAY}])
def test_bill_materialise_reports_schedule_end_once_not_payment(
        session, household, account, ending):
    row = bill(session, household, account, **ending)
    from app.persistence.models import RecurringCommitment
    c = session.get(RecurringCommitment, row.id)
    recurring.materialise(session, c, date(2030, 3, 1))
    recurring.materialise(session, c, date(2030, 3, 1))
    assert types(session) == ["bill.created"]  # Future generation is not an ended bill.
    financial_events.evaluate_bill_ends(session, household.id, today=DAY)
    assert types(session) == ["bill.created"]
    from app.core.scheduler import notify_tick
    notify_tick(session, notifier=None, now=datetime(2030, 1, 16, 0, 1))
    notify_tick(session, notifier=None, now=datetime(2030, 1, 16, 0, 2))
    assert types(session) == ["bill.created", "bill.ended"]
    ended = session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "bill.ended")).one()
    assert ended.payload["status"] == "ended"
    assert not any("paid" in t or "settled" in t for t in types(session))


def test_subscription_lifecycle_observation_is_not_settlement(session, household, account):
    s = subscription(session, household, account)
    subscriptions.patch_subscription(s.id, schemas.SubscriptionPatch(
        payee="Renamed", expected=Money.of_pence(1200), next_renewal_date=date(2030, 2, 15)),
        household, session, "test")
    subscriptions.patch_subscription(s.id, schemas.SubscriptionPatch(
        expected=Money.of_pence(1200), next_renewal_date=date(2030, 2, 15)),
        household, session, "test")
    subs.record_price(session, s, date(2030, 2, 15), 1200)
    subs.record_price(session, s, date(2030, 2, 15), 1200)
    subscriptions.cancel_subscription(s.id, schemas.SubscriptionCancelIn(date=date(2030, 3, 1)),
                                      household, session, "test")
    assert types(session) == ["subscription.created", "subscription.amount_changed",
                              "subscription.renewal_changed", "subscription.price_observed",
                              "subscription.renewal_changed", "subscription.cancelled"]
    for event in session.scalars(select(IntegrationEvent)):
        assert event.entity_id == s.id
        assert "NEVER ON BUS" not in str(event.payload)
        safe = outbox.visible_payload(event, False)
        assert "payee" not in safe and "amount_pence" not in safe


@pytest.mark.parametrize("detailed", [False, True])
@pytest.mark.parametrize("kind", ["bill", "subscription"])
def test_visibility_archived_private_deleted(session, household, account, detailed, kind):
    p = UserProfile(household_id=household.id, display_name="Owner", role=Role.member)
    session.add(p)
    session.flush()
    account.owner_profile_id = p.id
    row = (bill(session, household, account) if kind == "bill"
           else subscription(session, household, account))
    if detailed:
        outbox.set_detail(session, "detailed")
    assert integration.events(household, session).events == []
    visible = integration.events(household, session, p).events
    assert len(visible) == 1
    assert ("name" in visible[0].data or "payee" in visible[0].data) is detailed
    account.archived = True
    assert len(integration.events(household, session, p).events) == 1
    if kind == "bill":
        commitments.deactivate_commitment(row.id, household, session, "test", permanent=True)
    else:
        session.delete(row)
        session.flush()
    assert integration.events(household, session, p).events == []


def test_lifecycle_rollback_emits_nothing(session, household, account):
    session.commit()
    bill(session, household, account)
    subscription(session, household, account)
    assert types(session) == ["bill.created", "subscription.created"]
    session.rollback()
    assert types(session) == []

@pytest.mark.parametrize("detailed", [False, True])
def test_deleted_bill_events_stay_hidden_when_sqlite_reuses_id(
        session, household, account, detailed):
    owner = UserProfile(household_id=household.id, display_name="Original owner", role=Role.member)
    session.add(owner)
    session.flush()
    account.owner_profile_id = owner.id
    original = bill(session, household, account)
    original_event = session.scalar(select(IntegrationEvent.id))
    commitments.deactivate_commitment(original.id, household, session, "test", permanent=True)
    account.owner_profile_id = None
    replacement = bill(session, household, account)
    assert replacement.id == original.id  # SQLite can reuse the deleted final row's id.
    if detailed:
        outbox.set_detail(session, "detailed")
    visible = integration.events(household, session).events
    assert len(visible) == 1
    assert visible[0].seq > original_event


def test_bill_delete_visibility_watermark_rolls_back(session, household, account):
    original = bill(session, household, account)
    session.commit()
    commitments.deactivate_commitment(original.id, household, session, "test", permanent=True)
    assert integration.events(household, session).events == []
    session.rollback()
    assert [e.event_type for e in integration.events(household, session).events] == ["bill.created"]



def test_final_settled_bill_ends_after_occurrence_not_generation(session, household, account):
    from app.persistence.models import (
        Confidence,
        ExpectedMovement,
        ExpectedSource,
        Transaction,
        TransactionSource,
        TransactionStatus,
    )
    row = bill(session, household, account, occurrence_count=1)
    financial_events.evaluate_bill_ends(session, household.id, today=DAY)
    movement = ExpectedMovement(household_id=household.id, account_id=account.id,
        expected_date=DAY, amount_pence=-1000, label="Synthetic bill",
        confidence=Confidence.scheduled, source_type=ExpectedSource.commitment, source_id=row.id)
    txn = Transaction(account_id=account.id, date=DAY, amount_pence=-1000,
        payee="Synthetic bill", source=TransactionSource.starling, status=TransactionStatus.cleared)
    session.add_all([movement, txn])
    session.flush()
    settle.settle_occurrence(session, movement, txn, manual=True)
    assert "bill.ended" not in types(session)
    financial_events.evaluate_bill_ends(session, household.id, today=DAY + timedelta(days=1))
    financial_events.evaluate_bill_ends(session, household.id, today=DAY + timedelta(days=2))
    assert types(session).count("bill.ended") == 1


def test_bill_end_existing_baseline_quiet_and_rollback_retry(session, household, account):
    bill(session, household, account, end_date=DAY)
    financial_events.evaluate_bill_ends(session, household.id, today=DAY)
    session.commit()
    financial_events.evaluate_bill_ends(session, household.id, today=DAY + timedelta(days=1))
    assert types(session).count("bill.ended") == 1
    session.rollback()
    assert "bill.ended" not in types(session)
    financial_events.evaluate_bill_ends(session, household.id, today=DAY + timedelta(days=2))
    assert types(session).count("bill.ended") == 1
    # A first observation after a historical schedule ended seeds quietly.
    from app.persistence.models import RecurringCommitment
    other = RecurringCommitment(household_id=household.id, name="Existing before feature",
        account_id=account.id, amount_pence=-1000, frequency=Frequency.monthly,
        next_date=DAY, end_date=DAY)
    session.add(other)
    session.flush()
    financial_events.evaluate_bill_ends(session, household.id, today=DAY + timedelta(days=3))
    assert not session.scalar(select(IntegrationEvent.id).where(
        IntegrationEvent.event_type == "bill.ended", IntegrationEvent.entity_id == other.id))



def test_new_bill_offline_before_first_scheduler_tick_retains_end_event(
        session, household, account):
    row = bill(session, household, account, end_date=DAY)
    session.commit()
    financial_events.evaluate_bill_ends(session, household.id, today=DAY + timedelta(days=1))
    assert [e.event_type for e in integration.events(household, session).events] == [
        "bill.created", "bill.ended"]
    assert session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "bill.ended")).one().entity_id == row.id


def test_detected_subscription_creation_preserves_origin_metadata(session, household, account):
    sub = subs.create(session, household.id, payee="Detected subscription", source="detected",
        frequency=Frequency.monthly, expected_pence=1000, next_renewal_date=DAY,
        account_id=account.id)
    event = session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "subscription.created")).one()
    assert event.entity_id == sub.id
    assert outbox.visible_payload(event, False)["source"] == "detected"


@pytest.mark.parametrize("detailed", [False, True])
@pytest.mark.parametrize("kind", ["bill", "subscription"])
def test_original_private_scope_survives_account_reclassification(
        session, household, account, detailed, kind):
    owner = UserProfile(household_id=household.id, display_name="Original", role=Role.member)
    other = UserProfile(household_id=household.id, display_name="Other", role=Role.member)
    session.add_all([owner, other])
    session.flush()
    account.owner_profile_id = owner.id
    if kind == "bill":
        bill(session, household, account, end_date=DAY)
        financial_events.evaluate_bill_ends(session, household.id, today=DAY + timedelta(days=1))
    else:
        item = subscription(session, household, account)
        subs.record_price(session, item, DAY, 1000)
        subscriptions.cancel_subscription(item.id, schemas.SubscriptionCancelIn(date=DAY),
                                          household, session, "test")
    if detailed:
        outbox.set_detail(session, "detailed")
    before = integration.events(household, session, owner).events
    assert before
    assert all("_scope_owner_profile_id" not in event.data for event in before)
    account.owner_profile_id = None
    # The HA service has no member identity; making the account shared must not
    # turn its queued private history into a shared event stream.
    assert integration.events(household, session).events == []
    assert integration.events(household, session, other).events == []
    assert len(integration.events(household, session, owner).events) == len(before)
    account.owner_profile_id = other.id
    assert integration.events(household, session, other).events == []
    assert integration.events(household, session, owner).events == []


def test_accountless_subscription_retains_existing_shared_scope_without_metadata_leak(
        session, household):
    subs.create(session, household.id, payee="Shared unassigned subscription",
        frequency=Frequency.monthly, expected_pence=1000, next_renewal_date=DAY)
    outbox.set_detail(session, "detailed")
    events = integration.events(household, session).events
    assert len(events) == 1
    assert "_scope_owner_profile_id" not in events[0].data
