from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import select

from app.api.payroll_scope import authorize_employment
from app.api.v1.payslips import _notify_review_needed
from app.domain import outbox, payroll, payrun, seasonal
from app.persistence.models import (
    Account,
    Employment,
    Gift,
    IntegrationEvent,
    Payslip,
    PayslipStatus,
    PurchaseStatus,
    SeasonalEvent,
    UserProfile,
)


def employment(session, household):
    owner = UserProfile(household_id=household.id, display_name="Synthetic", role="member")
    session.add(owner)
    session.flush()
    e = Employment(
        household_id=household.id,
        profile_id=owner.id,
        employer="Synthetic",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
    )
    session.add(e)
    session.flush()
    payroll.add_salary_revision(session, e, date(2025, 1, 1), 3600000)
    payroll.add_tax_code_revision(session, e, date(2025, 1, 1), "1257L")
    return e


def generate(session, e):
    return payrun.generate(
        session, e, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )


def test_payroll_transitions_emit_deduplicated_safe_events(session, household):
    e = employment(session, household)
    run = generate(session, e)
    assert generate(session, e).id == run.id
    payrun.reconcile(session, run, {})
    events = list(session.scalars(select(IntegrationEvent).order_by(IntegrationEvent.id)))
    assert [event.event_type for event in events] == ["payroll.generated", "payroll.reconciled"]
    assert all(event.entity_kind == "payroll_run" and event.entity_id == run.id for event in events)
    assert all(
        outbox.visible_payload(event, False)
        == {"source": "payroll", "status": event.payload["status"], "date": "2025-04-28"}
        for event in events
    )
    assert all(event.payload["owner_profile_id"] == e.profile_id for event in events)


def test_payslip_review_event_is_once_per_artifact(session, household):
    e = employment(session, household)
    run = generate(session, e)
    row = Payslip(
        household_id=household.id,
        payroll_run_id=run.id,
        original_name="synthetic.pdf",
        private_path="/tmp/synthetic-unused.pdf",
        sha256="a" * 64,
        size_bytes=1,
        media_type="application/pdf",
        status=PayslipStatus.needs_review,
    )
    session.add(row)
    session.flush()
    _notify_review_needed(session, household.id, row)
    _notify_review_needed(session, household.id, row)
    events = list(
        session.scalars(
            select(IntegrationEvent).where(IntegrationEvent.event_type == "payslip.review_required")
        )
    )
    assert len(events) == 1
    assert events[0].entity_id == row.id
    assert "private_path" not in events[0].payload


def test_gift_transitions_emit_once_and_keep_private_scope(session, household):
    e = employment(session, household)
    event = SeasonalEvent(
        household_id=household.id,
        owner_profile_id=e.profile_id,
        year=2025,
        type="synthetic",
        name="Synthetic",
        budget_pence=10000,
    )
    session.add(event)
    session.flush()
    gift = Gift(
        event_id=event.id,
        recipient="Synthetic",
        idea="Synthetic",
        planned_pence=1000,
        actual_pence=1000,
        purchase_status=PurchaseStatus.planned,
    )
    session.add(gift)
    session.flush()
    seasonal.transition_purchase(gift, PurchaseStatus.purchased)
    seasonal.mark_paid(session, gift)
    seasonal.mark_paid(session, gift)
    events = list(session.scalars(select(IntegrationEvent).order_by(IntegrationEvent.id)))
    assert [row.event_type for row in events] == ["gift.purchased", "gift.paid"]
    assert all(
        row.entity_id == gift.id and row.payload["owner_profile_id"] == e.profile_id
        for row in events
    )
    assert all("amount_pence" not in outbox.visible_payload(row, False) for row in events)


def test_ha_service_overtime_only_targets_shared_destination(session, household):
    e = employment(session, household)
    account = Account(
        household_id=household.id, name="Synthetic", type="joint", opening_date=date(2025, 1, 1)
    )
    session.add(account)
    session.flush()
    e.paid_into_account_id = account.id
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [],
            "path": f"/api/v1/employments/{e.id}/overtime-entries",
            "path_params": {"emp_id": e.id},
        }
    )
    request.state.service_authenticated = True
    authorize_employment(request, household, session, None)
    account.owner_profile_id = e.profile_id
    with pytest.raises(HTTPException) as error:
        authorize_employment(request, household, session, None)
    assert error.value.status_code == 403


def test_real_goal_contributions_emit_threshold_crossings_once(session, household):
    from app.domain import goals, transfers
    from app.persistence.models import GoalType, SavingsGoal, Transaction, TransactionStatus

    e = employment(session, household)
    debit_account = Account(
        household_id=household.id,
        name="Synthetic current",
        type="joint",
        opening_date=date(2025, 1, 1),
    )
    pot = Account(
        household_id=household.id,
        name="Synthetic pot",
        type="personal",
        owner_profile_id=e.profile_id,
        opening_date=date(2025, 1, 1),
    )
    session.add_all([debit_account, pot])
    session.flush()
    goal = SavingsGoal(
        household_id=household.id,
        name="Synthetic goal",
        goal_type=GoalType.holiday,
        account_id=pot.id,
        start_date=date(2025, 1, 1),
        target_pence=100000,
    )
    session.add(goal)
    session.flush()
    assert not list(session.scalars(select(IntegrationEvent)))
    for amount in (30000, 50000, 20000):
        debit = Transaction(
            account_id=debit_account.id,
            date=date(2025, 4, 10),
            amount_pence=-amount,
            payee="Synthetic",
            status=TransactionStatus.cleared,
        )
        credit = Transaction(
            account_id=pot.id,
            date=date(2025, 4, 10),
            amount_pence=amount,
            payee="Synthetic",
            status=TransactionStatus.cleared,
        )
        session.add_all([debit, credit])
        session.flush()
        transfers.create_transfer(session, household.id, debit, credit)
    rows = list(
        session.scalars(
            select(IntegrationEvent)
            .where(IntegrationEvent.event_type == "goal.milestone")
            .order_by(IntegrationEvent.id)
        )
    )
    assert [row.payload["state"] for row in rows] == ["25", "50", "75", "100"]
    assert all(
        row.entity_id == goal.id and row.payload["owner_profile_id"] == e.profile_id for row in rows
    )
    goals.emit_contribution_milestones(session, credit)
    assert len(list(session.scalars(select(IntegrationEvent)))) == 4
