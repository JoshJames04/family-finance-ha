"""Canonical payroll change events, never guessed salary receipts (#524)."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.v1 import integration
from app.domain import outbox, overtime, pay_calendar, paydays, payroll, schedule
from app.persistence.models import (
    Account,
    Employment,
    ExpectedMovement,
    IntegrationEvent,
    PayrollRun,
    PayrollRunStatus,
    Role,
    UserProfile,
)

TODAY = date(2026, 1, 1)
PAY = date(2026, 1, 28)


@pytest.fixture()
def employment(session, household):
    owner = UserProfile(household_id=household.id, display_name="Owner", role=Role.member)
    account = Account(household_id=household.id, name="Shared destination", type="joint",
                      opening_date=TODAY)
    session.add_all([owner, account])
    session.flush()
    emp = Employment(household_id=household.id, profile_id=owner.id, employer="Synthetic employer",
        jurisdiction="england_wales", contracted_hours=Decimal("37.5"), deduction_mode="gross_only",
        paid_into_account_id=account.id, payday_rule=pay_calendar.serialize_rule(paydays.PaydayRule(
            kind=paydays.RuleKind.fixed_day, day=28, adjust=paydays.ShiftPolicy.previous)))
    session.add(emp)
    session.flush()
    payroll.add_salary_revision(session, emp, TODAY, 3_600_000)
    return emp


def events(session, event_type):
    return list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == event_type).order_by(IntegrationEvent.id)))


def test_overtime_recorded_on_actual_create_not_edits_or_approval(session, employment):
    entry = overtime.create_entry(session, employment, work_date=TODAY, hours=Decimal("2"),
                                  notes="PRIVATE NOTES")
    overtime.update_entry(session, entry, employment, hours=Decimal("2"))
    overtime.submit(entry)
    overtime.approve(entry)
    rows = events(session, "overtime.recorded")
    assert len(rows) == 1
    assert rows[0].entity_kind == "employment" and rows[0].entity_id == employment.id
    assert rows[0].payload["status"] == "draft"
    assert "PRIVATE NOTES" not in str(rows[0].payload)
    assert "paid" not in str(rows[0].payload)


def test_employment_estimate_changed_matches_canonical_values_and_replay(session, employment):
    schedule.rematerialize_employment(session, employment, today=TODAY, horizon_days=30)
    assert events(session, "payroll.estimate_changed") == []
    payroll.add_salary_revision(session, employment, date(2026, 1, 2), 4_200_000)
    schedule.rematerialize_employment(session, employment, today=TODAY, horizon_days=30)
    schedule.rematerialize_employment(session, employment, today=TODAY, horizon_days=30)
    rows = events(session, "payroll.estimate_changed")
    assert len(rows) == 1
    assert rows[0].payload["old_pence"] == 300_000
    current = session.scalars(select(ExpectedMovement)).one()
    assert rows[0].payload["amount_pence"] == current.amount_pence == 350_000
    assert rows[0].payload["status"] == "estimate"
    assert rows[0].payload["expected_date"] == PAY.isoformat()


def test_payroll_run_estimate_changed_excludes_first_and_actual_values(session, employment):
    run = PayrollRun(employment_id=employment.id, period_start=TODAY, period_end=date(2026, 1, 31),
        pay_date=PAY, status=PayrollRunStatus.forecast, tax_year="2025/26", rule_set_version=1,
        gross_pence=300_000, net_pence=250_000)
    session.add(run)
    session.flush()
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    assert events(session, "payroll.estimate_changed") == []
    run.net_pence = 255_000
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    assert len(events(session, "payroll.estimate_changed")) == 1
    run.status = PayrollRunStatus.reconciled
    run.net_pence = 257_000
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    assert len(events(session, "payroll.estimate_changed")) == 1


@pytest.mark.parametrize("detailed", [False, True])
def test_payroll_events_private_despite_shared_destination(
        session, household, employment, detailed):
    overtime.create_entry(session, employment, work_date=TODAY, hours=Decimal("2"), notes="SECRET")
    schedule.rematerialize_employment(session, employment, today=TODAY, horizon_days=30)
    payroll.add_salary_revision(session, employment, date(2026, 1, 2), 4_200_000)
    schedule.rematerialize_employment(session, employment, today=TODAY, horizon_days=30)
    if detailed:
        outbox.set_detail(session, "detailed")
    assert integration.events(household, session).events == []
    owner = session.get(UserProfile, employment.profile_id)
    visible = integration.events(household, session, owner).events
    assert {e.event_type for e in visible} == {"overtime.recorded", "payroll.estimate_changed"}
    for event in visible:
        assert "SECRET" not in str(event.data)
        assert ("name" in event.data) is detailed
        if not detailed:
            assert "hours" not in event.data and "amount_pence" not in event.data


def test_payroll_writers_and_events_roll_back_together(session, household, employment):
    session.commit()
    overtime.create_entry(session, employment, work_date=TODAY, hours=Decimal("2"))
    schedule.rematerialize_employment(session, employment, today=TODAY, horizon_days=30)
    payroll.add_salary_revision(session, employment, date(2026, 1, 2), 4_200_000)
    schedule.rematerialize_employment(session, employment, today=TODAY, horizon_days=30)
    assert len(events(session, "overtime.recorded")) == 1
    assert len(events(session, "payroll.estimate_changed")) == 1
    session.rollback()
    assert list(session.scalars(select(IntegrationEvent))) == []
    assert list(session.scalars(select(ExpectedMovement))) == []
