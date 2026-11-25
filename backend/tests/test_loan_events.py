"""Loan automation derives from the same dated balance as the Loans page."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import financial_events, outbox
from app.main import create_app
from app.persistence.models import (
    Household,
    IntegrationEvent,
    Loan,
    LoanDirection,
    Repayment,
    Role,
    UserProfile,
)

TODAY = date(2026, 1, 15)


def make_loan(session, household):
    loan = Loan(household_id=household.id, party="Synthetic lender",
                direction=LoanDirection.we_owe, principal_pence=10000,
                start_date=date(2026, 1, 1), active=True, notes="Never in event")
    session.add(loan)
    session.flush()
    return loan


def events(session):
    return list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type.like("loan.%")).order_by(IntegrationEvent.id)))


def evaluate(session, loan, today=TODAY):
    financial_events.evaluate_loan(session, loan, today=today)


def repay(session, loan, amount, on=TODAY):
    row = Repayment(loan_id=loan.id, date=on, amount_pence=amount)
    session.add(row)
    session.flush()
    return row


def test_seed_then_full_repayment_is_atomic_detailed_and_deduplicated(session, household):
    loan = make_loan(session, household)
    evaluate(session, loan)
    assert events(session) == []
    repay(session, loan, 10000)
    evaluate(session, loan)
    evaluate(session, loan)
    result = events(session)
    assert [e.event_type for e in result] == ["loan.balance_changed", "loan.fully_repaid"]
    assert result[0].payload["old_outstanding_pence"] == 10000
    assert result[0].payload["outstanding_pence"] == 0
    assert result[1].payload["name"] == "Synthetic lender"
    assert result[1].entity_kind == "loan" and result[1].entity_id == loan.id
    assert outbox.visible_payload(result[1], False) == {
        "source": "loan", "status": "fully_repaid", "date": TODAY.isoformat(),
        "direction": "we_owe", "currency": "GBP",
    }
    assert "notes" not in outbox.visible_payload(result[1], True)
    session.rollback()
    assert events(session) == []


def test_partial_deletion_and_new_completion_are_distinct_transitions(session, household):
    loan = make_loan(session, household)
    evaluate(session, loan)
    first = repay(session, loan, 4000)
    evaluate(session, loan)
    second = repay(session, loan, 6000)
    evaluate(session, loan)
    session.delete(second)
    session.flush()
    evaluate(session, loan)
    repay(session, loan, 6000)
    evaluate(session, loan)
    assert [e.payload["outstanding_pence"] for e in events(session)
            if e.event_type == "loan.balance_changed"] == [6000, 0, 6000, 0]
    assert sum(e.event_type == "loan.fully_repaid" for e in events(session)) == 2
    assert first.amount_pence == 4000


def test_future_repayment_counts_only_when_its_date_arrives(session, household):
    loan = make_loan(session, household)
    evaluate(session, loan)
    repay(session, loan, 10000, TODAY + timedelta(days=1))
    evaluate(session, loan)
    assert events(session) == []
    evaluate(session, loan, TODAY + timedelta(days=1))
    assert [e.event_type for e in events(session)] == [
        "loan.balance_changed", "loan.fully_repaid"]


def test_owner_change_reseeds_and_private_events_remain_filtered(session, household):
    loan = make_loan(session, household)
    evaluate(session, loan)
    owner = UserProfile(household_id=household.id, display_name="Synthetic member",
                        role=Role.member)
    session.add(owner)
    session.flush()
    loan.owner_profile_id = owner.id
    repay(session, loan, 4000)
    evaluate(session, loan)
    assert events(session) == []
    repay(session, loan, 1000)
    evaluate(session, loan)
    event = events(session)[0]
    assert not outbox.event_visible(session, event, None)
    assert outbox.event_visible(session, event, owner.id)
    assert not outbox.event_visible(session, event, owner.id + 1)
    other = Household(name="Other synthetic household")
    session.add(other)
    session.flush()
    assert outbox.feed(session, other.id)[0] == []
    # Reclassifying the current record must not reclassify its old private event.
    loan.owner_profile_id = None
    session.flush()
    assert not outbox.event_visible(session, event, None)
    assert "_scope_owner_profile_id" not in outbox.visible_payload(event, True)


def test_household_evaluation_and_inactive_loans(session, household):
    loan = make_loan(session, household)
    financial_events.evaluate_household(session, household.id, today=TODAY)
    repay(session, loan, 4000)
    financial_events.evaluate_household(session, household.id, today=TODAY)
    assert len(events(session)) == 1
    loan.active = False
    repay(session, loan, 6000)
    evaluate(session, loan)
    assert len(events(session)) == 1


def test_api_records_transitions_between_ticks_and_invalid_writes_do_not_emit(tmp_path):
    with TestClient(create_app(Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as client:
        hid = client.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
        headers = {"X-Household-Id": str(hid)}
        loan = client.post("/api/v1/loans", headers=headers, json={
            "party": "Synthetic lender", "direction": "they_owe",
            "principal": 10000, "start_date": "2025-01-01"})
        assert loan.status_code == 201
        lid = loan.json()["id"]
        route = f"/api/v1/loans/{lid}/repayments"
        payment = client.post(route, headers=headers, json={
            "date": "2025-01-02", "amount": 10000})
        assert payment.status_code == 201
        rid = payment.json()["id"]
        assert client.delete(f"{route}/{rid}", headers=headers).status_code == 204
        assert client.post(route, headers=headers, json={
            "date": "2025-01-02", "amount": -1}).status_code == 409
        feed = client.get("/api/v1/integration/events", headers=headers).json()["events"]
        assert [e["event_type"] for e in feed if e["event_type"].startswith("loan.")] == [
            "loan.balance_changed", "loan.fully_repaid", "loan.balance_changed"]


def test_interest_and_restart_use_persisted_balance_without_replay(session, household):
    loan = make_loan(session, household)
    loan.principal_pence = 365000
    loan.interest_bps_pa = 1000
    evaluate(session, loan)
    evaluate(session, loan, TODAY + timedelta(days=1))
    assert events(session)[0].payload["outstanding_pence"] == 366500
    assert events(session)[0].payload["old_outstanding_pence"] == 366400
    session.commit()
    with Session(session.get_bind()) as restarted:
        evaluate(restarted, restarted.get(Loan, loan.id), TODAY + timedelta(days=1))
        assert len(events(restarted)) == 1


def test_reactivation_seeds_without_replaying_inactive_changes(session, household):
    loan = make_loan(session, household)
    evaluate(session, loan)
    loan.active = False
    evaluate(session, loan)
    repay(session, loan, 4000)
    loan.active = True
    evaluate(session, loan)
    assert events(session) == []
    repay(session, loan, 1000)
    evaluate(session, loan)
    assert events(session)[0].payload["old_outstanding_pence"] == 6000
    assert events(session)[0].payload["outstanding_pence"] == 5000

def test_voided_correction_emits_loan_change_in_transaction(session, household):
    from app.domain import loans, periods, transactions
    from app.persistence.models import Account, Transaction, TransactionStatus

    loan = make_loan(session, household)
    account = Account(household_id=household.id, name="Loan account", type="joint",
                      opening_date=TODAY)
    session.add(account)
    session.flush()
    original = Transaction(account_id=account.id, date=TODAY, amount_pence=-3000,
                           payee="Loan payment", status=TransactionStatus.reconciled)
    session.add(original)
    session.flush()
    loans.link_repayment(session, repay(session, loan, 3000), original)
    financial_events.evaluate_loan(session, loan)
    replacement = transactions.correct(session, original, "label")
    assert events(session) == []
    periods.transition(session, replacement, TransactionStatus.void)
    assert [(e.event_type, e.payload["outstanding_pence"]) for e in events(session)] == [
        ("loan.balance_changed", 10000)]
    session.rollback()
    assert events(session) == []

def test_reclassifying_a_repayment_as_transfer_emits_loan_change(session, household):
    from app.domain import loans, transfers
    from app.persistence.models import Account, Transaction

    loan = make_loan(session, household)
    checking, saving = (Account(household_id=household.id, name=name, type="joint",
                                opening_date=TODAY) for name in ("First", "Second"))
    session.add_all([checking, saving])
    session.flush()
    debit = Transaction(account_id=checking.id, date=TODAY, amount_pence=-3000, payee="Payment")
    credit = Transaction(account_id=saving.id, date=TODAY, amount_pence=3000, payee="Receipt")
    session.add_all([debit, credit])
    session.flush()
    loans.link_repayment(session, repay(session, loan, 3000), debit)
    transfers.create_transfer(session, household.id, debit, credit)
    assert [event.payload["outstanding_pence"] for event in events(session)] == [10000]
