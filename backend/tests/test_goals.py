"""Issue #91 — savings goals: derived contribution ledger, sinking-fund
maths, expected-movement materialisation."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import goals, schedule
from app.main import create_app
from app.persistence.models import (
    Account,
    ExpectedMovement,
    ExpectedSource,
    GoalType,
    Household,
    IntegrationEvent,
    SavingsGoal,
    Transaction,
    TransactionStatus,
    Transfer,
)

TODAY = date(2025, 4, 10)


def test_completion_event_is_canonical_private_and_replay_safe(
        session, household, pot, main):
    from app.domain import outbox

    goal = _goal(session, household, pot, target=10_000)
    first = _contribution(session, household, main, pot, 9_999, TODAY)
    goals.emit_contribution_milestones(session, first, today=TODAY)
    assert not session.scalar(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.completed"))
    credit = _contribution(session, household, main, pot, 1, TODAY)
    goals.emit_contribution_milestones(session, credit, today=TODAY)
    goals.emit_contribution_milestones(session, credit, today=TODAY)
    events = list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.completed")))
    assert len(events) == 1
    event = events[0]
    assert event.entity_id == goal.id
    assert event.payload["name"] == "Reserve"
    assert event.payload["amount_pence"] == 10_000
    assert event.payload["target_pence"] == 10_000
    assert outbox.visible_payload(event, False) == {
        "source": "savings_goal", "status": "completed", "date": "2025-04-10",
        "currency": "GBP"}
    assert outbox.visible_payload(event, True)["amount_gbp"] == "100.00"
    later = _contribution(session, household, main, pot, 100, TODAY)
    goals.emit_contribution_milestones(session, later, today=TODAY)
    # Completion is a lifetime goal occurrence, including a later re-crossing.
    first.status = TransactionStatus.void
    session.flush()
    replacement = _contribution(session, household, main, pot, 9_999, TODAY)
    goals.emit_contribution_milestones(session, replacement, today=TODAY)
    assert len(list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.completed")))) == 1


def test_completion_event_rolls_back_with_contribution(session, household, pot, main):
    _goal(session, household, pot, target=10_000)
    with session.begin_nested() as transaction:
        credit = _contribution(session, household, main, pot, 10_000, TODAY)
        goals.emit_contribution_milestones(session, credit, today=TODAY)
        assert session.scalar(select(IntegrationEvent).where(
            IntegrationEvent.event_type == "goal.completed")) is not None
        transaction.rollback()
    assert list(session.scalars(select(IntegrationEvent))) == []


def test_future_contribution_completes_only_on_effective_date(session, household, pot, main):
    from app.domain import financial_events

    _goal(session, household, pot, target=10_000)
    credit = _contribution(session, household, main, pot, 10_000, date(2025, 4, 12))
    goals.emit_contribution_milestones(session, credit, today=TODAY)
    assert list(session.scalars(select(IntegrationEvent))) == []
    financial_events.evaluate_household(session, household.id, today=date(2025, 4, 12))
    financial_events.evaluate_household(session, household.id, today=date(2025, 4, 13))
    events = list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.completed")))
    assert len(events) == 1
    assert events[0].payload["date"] == "2025-04-12"
    milestones = list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.milestone")))
    assert {event.payload["state"] for event in milestones} == {"25", "50", "75", "100"}


def test_future_link_does_not_swallow_behind_plan_transition(session, household, pot, main):
    from app.domain import financial_events

    _goal(session, household, pot, target=40_000, target_date=date(2025, 7, 1))
    _contribution(session, household, main, pot, 20_000, date(2025, 1, 2))
    financial_events.evaluate_household(session, household.id, today=date(2025, 3, 31))
    future = _contribution(session, household, main, pot, 100, date(2025, 5, 1))
    goals.emit_contribution_milestones(session, future, today=date(2025, 4, 1))
    financial_events.evaluate_household(session, household.id, today=date(2025, 4, 1))
    events = list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.behind_plan")))
    assert len(events) == 1


def test_out_of_order_link_uses_chronological_completion_date(session, household, pot, main):
    _goal(session, household, pot, target=10_000)
    later = _contribution(session, household, main, pot, 6_000, date(2025, 2, 1))
    goals.emit_contribution_milestones(session, later, today=TODAY)
    earlier = _contribution(session, household, main, pot, 6_000, date(2025, 1, 2))
    goals.emit_contribution_milestones(session, earlier, today=TODAY)
    event = session.scalar(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.completed"))
    assert event is not None
    assert event.payload["date"] == "2025-02-01"
    assert event.payload["amount_pence"] == 12_000


def test_private_goal_completion_is_not_visible_to_shared_service(
        session, household, pot, main):
    from app.domain import outbox
    from app.persistence.models import UserProfile

    owner = UserProfile(household_id=household.id, display_name="Synthetic owner")
    session.add(owner)
    session.flush()
    pot.owner_profile_id = owner.id
    _goal(session, household, pot, target=10_000)
    credit = _contribution(session, household, main, pot, 10_000, TODAY)
    goals.emit_contribution_milestones(session, credit, today=TODAY)
    event = session.scalar(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.completed"))
    assert event is not None
    assert not outbox.event_visible(session, event, None)
    assert outbox.event_visible(session, event, owner.id)
    pot.owner_profile_id = None
    session.flush()
    assert not outbox.event_visible(session, event, None)
    assert "_scope_owner_profile_id" not in outbox.visible_payload(event, True)


def test_goal_falling_behind_uses_persisted_canonical_progress(session, household, pot, main):
    from app.domain import financial_events

    goal = _goal(session, household, pot, target=40_000, target_date=date(2025, 7, 1))
    _contribution(session, household, main, pot, 20_000, date(2025, 1, 2))
    financial_events.evaluate_household(session, household.id, today=date(2025, 3, 31))
    assert list(session.scalars(select(IntegrationEvent))) == []
    financial_events.evaluate_household(session, household.id, today=date(2025, 4, 1))
    financial_events.evaluate_household(session, household.id, today=date(2025, 4, 2))
    events = list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.behind_plan")))
    assert len(events) == 1
    assert events[0].entity_id == goal.id
    assert events[0].payload["remaining_pence"] == 20_000


def test_funded_goal_does_not_become_behind_plan_after_its_deadline(
        session, household, pot, main):
    from app.domain import financial_events

    _goal(session, household, pot, target=40_000, target_date=date(2025, 4, 1))
    _contribution(session, household, main, pot, 40_000, date(2025, 1, 2))
    financial_events.evaluate_household(session, household.id, today=date(2025, 3, 31))
    financial_events.evaluate_household(session, household.id, today=date(2025, 8, 1))
    assert list(session.scalars(select(IntegrationEvent))) == []


def test_goal_target_approaching_catches_up_once_per_target_date(session, household, pot):
    from app.domain import financial_events, outbox

    goal = _goal(session, household, pot, target=10_000, target_date=date(2025, 4, 30))
    financial_events.evaluate_household(session, household.id, today=date(2025, 4, 25))
    assert list(session.scalars(select(IntegrationEvent))) == []
    # Catch up after an offline day, then do not repeat on the due day.
    financial_events.evaluate_household(session, household.id, today=date(2025, 4, 28))
    financial_events.evaluate_household(session, household.id, today=date(2025, 4, 30))
    events = list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.target_approaching")))
    assert len(events) == 1
    assert outbox.visible_payload(events[0], False) == {
        "source": "savings_goal", "status": "approaching", "date": "2025-04-28",
        "expected_date": "2025-04-30", "lead_days": 3, "currency": "GBP"}
    goal.target_date = date(2025, 5, 31)
    financial_events.evaluate_household(session, household.id, today=date(2025, 5, 28))
    assert len(list(session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "goal.target_approaching")))) == 2


@pytest.mark.parametrize("state", ["inactive", "funded", "undated", "future", "past"])
def test_goal_deadline_ignores_ineligible_states(session, household, pot, main, state):
    from app.domain import financial_events

    goal = _goal(session, household, pot, target=10_000, target_date=date(2025, 4, 12))
    if state == "inactive":
        goal.active = False
    elif state == "funded":
        _contribution(session, household, main, pot, 10_000, TODAY)
    elif state == "undated":
        goal.target_date = None
    elif state == "future":
        goal.start_date = date(2025, 4, 11)
    else:
        goal.target_date = date(2025, 4, 9)
    financial_events.evaluate_household(session, household.id, today=TODAY)
    assert list(session.scalars(select(IntegrationEvent))) == []


def test_goal_deadline_rolls_back_and_respects_private_visibility(session, household, pot):
    from app.domain import financial_events, outbox
    from app.persistence.models import UserProfile

    owner = UserProfile(household_id=household.id, display_name="Synthetic owner")
    session.add(owner)
    session.flush()
    pot.owner_profile_id = owner.id
    _goal(session, household, pot, target=10_000, target_date=date(2025, 4, 12))
    household_id = household.id
    with session.begin_nested() as transaction:
        financial_events.evaluate_household(session, household.id, today=TODAY)
        event = session.scalar(select(IntegrationEvent).where(
            IntegrationEvent.event_type == "goal.target_approaching"))
        assert event is not None
        assert not outbox.event_visible(session, event, None)
        assert outbox.event_visible(session, event, owner.id)
        transaction.rollback()
    session.expunge_all()
    assert list(session.scalars(select(IntegrationEvent))) == []
    financial_events.evaluate_household(session, household_id, today=TODAY)
    assert len(list(session.scalars(select(IntegrationEvent)))) == 1


@pytest.fixture()
def pot(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Holiday pot",
                type="savings", opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


@pytest.fixture()
def main(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Joint",
                type="joint", opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _goal(session: Session, household: Household, pot: Account,
          target: int = 120_000, target_date: date | None = None,
          gtype: GoalType = GoalType.annual_reserve) -> SavingsGoal:
    g = SavingsGoal(
        household_id=household.id, name="Reserve", goal_type=gtype,
        target_pence=target, target_date=target_date,
        start_date=date(2025, 1, 1), account_id=pot.id, active=True)
    session.add(g)
    session.flush()
    return g


def _contribution(session: Session, household: Household,
                  main: Account, pot: Account, pence: int,
                  on: date) -> Transaction:
    t = Transfer(household_id=household.id)
    session.add(t)
    session.flush()
    session.add(Transaction(
        account_id=main.id, date=on, amount_pence=-pence,
        payee="To pot", transfer_id=t.id,
        status=TransactionStatus.cleared, source="manual"))
    credit = Transaction(
        account_id=pot.id, date=on, amount_pence=pence,
        payee="To pot", transfer_id=t.id,
        status=TransactionStatus.cleared, source="manual")
    session.add(credit)
    session.flush()
    return credit


class TestLedger:
    def test_contribution_is_real_transfer_visible_in_ledger(
            self, session: Session, household: Household,
            pot: Account, main: Account) -> None:
        """AC: contribution = real transfer, visible in goal view."""
        g = _goal(session, household, pot)
        credit = _contribution(session, household, main, pot, 10_000,
                               date(2025, 3, 1))
        rows = goals.ledger(session, g)
        assert [t.id for t in rows] == [credit.id]
        assert goals.contributed_pence(session, g) == 10_000

    def test_non_transfer_inflow_not_a_contribution(
            self, session: Session, household: Household,
            pot: Account, main: Account) -> None:
        g = _goal(session, household, pot)
        _contribution(session, household, main, pot, 5_000,
                      date(2025, 3, 1))
        session.add(Transaction(  # interest — not a transfer leg
            account_id=pot.id, date=date(2025, 3, 2),
            amount_pence=500, payee="Interest",
            status=TransactionStatus.cleared, source="manual"))
        session.flush()
        assert goals.contributed_pence(session, g) == 5_000


class TestSinkingMaths:
    def test_monthly_need_divides_remaining(
            self, session: Session, household: Household,
            pot: Account) -> None:
        """AC: annual-reserve computes monthly need from target+date."""
        g = _goal(session, household, pot, target=120_000,
                  target_date=date(2026, 4, 1))
        # Apr 2025 → Apr 2026 = 12 months → £1,000/mo
        assert goals.monthly_need(session, g, TODAY) == 10_000

    def test_monthly_need_rounds_up(
            self, session: Session, household: Household,
            pot: Account) -> None:
        g = _goal(session, household, pot, target=100_001,
                  target_date=date(2025, 6, 1))
        # 2 months → 50000.5 → ceil 50001
        assert goals.monthly_need(session, g, TODAY) == 50_001

    def test_contributions_reduce_need(
            self, session: Session, household: Household,
            pot: Account, main: Account) -> None:
        g = _goal(session, household, pot, target=120_000,
                  target_date=date(2026, 4, 1))
        _contribution(session, household, main, pot, 60_000,
                      date(2025, 3, 1))
        assert goals.monthly_need(session, g, TODAY) == 5_000

    def test_undated_goal_has_no_monthly_need(
            self, session: Session, household: Household,
            pot: Account) -> None:
        g = _goal(session, household, pot, target=50_000)
        assert goals.monthly_need(session, g, TODAY) is None

    def test_funded_goal_needs_nothing(
            self, session: Session, household: Household,
            pot: Account, main: Account) -> None:
        g = _goal(session, household, pot, target=5_000,
                  target_date=date(2026, 1, 1))
        _contribution(session, household, main, pot, 5_000,
                      date(2025, 3, 1))
        assert goals.monthly_need(session, g, TODAY) == 0
        assert goals.progress(session, g, TODAY).pct == 100


class TestMaterialise:
    @pytest.mark.parametrize("deadline, due", [
        (date(2025, 4, 9), None), (TODAY, TODAY), (date(2025, 4, 25), date(2025, 4, 25)),
    ])
    def test_deadline_month(self, session, household, pot, deadline, due):
        goal = _goal(session, household, pot, target=125, target_date=deadline)
        schedule.rematerialize_goal(session, goal, today=TODAY)
        rows = session.execute(select(ExpectedMovement.expected_date,
                                      ExpectedMovement.amount_pence)).all()
        assert rows == ([(due, 125)] if due else [])

    def test_sinking_schedule_emits_estimated(
            self, session: Session, household: Household,
            pot: Account) -> None:
        g = _goal(session, household, pot, target=60_000,
                  target_date=date(2025, 8, 1),
                  gtype=GoalType.emergency)
        n = schedule.rematerialize_goal(session, g, today=TODAY)
        rows = list(session.execute(select(ExpectedMovement).where(
            ExpectedMovement.source_type ==
            ExpectedSource.savings_goal)).scalars())
        assert n == len(rows) == 4  # May–Aug
        assert all(m.expected_date.day == 1 for m in rows)
        assert {m.amount_pence for m in rows} == {15_000}
        assert all(m.account_id == pot.id for m in rows)

    def test_deactivate_drops_future(
            self, session: Session, household: Household,
            pot: Account) -> None:
        g = _goal(session, household, pot, target=60_000,
                  target_date=date(2025, 8, 1))
        schedule.rematerialize_goal(session, g, today=TODAY)
        g.active = False
        assert schedule.rematerialize_goal(
            session, g, today=TODAY) == 0
        assert list(session.execute(select(ExpectedMovement).where(
            ExpectedMovement.source_type ==
            ExpectedSource.savings_goal)).scalars()) == []


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def test_goal_progress_flow(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        main_id = client.post("/api/v1/accounts", json={
            "name": "Joint", "type": "joint", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        pot = client.post("/api/v1/accounts", json={
            "name": "Pot", "type": "savings", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        g = client.post("/api/v1/savings-goals", headers=h, json={
            "name": "Holiday", "goal_type": "holiday",
            "target": 120000, "target_date": "2027-01-01",
            "start_date": "2025-01-01", "account_id": pot})
        assert g.status_code == 201, g.json()
        gid = g.json()["id"]
        # a real contribution via a linked transfer
        d = client.post("/api/v1/transactions", headers=h, json={
            "account_id": pot, "date": "2025-06-01",
            "amount": 30000, "payee": "Save"})
        c = client.post("/api/v1/transactions", headers=h, json={
            "account_id": main_id, "date": "2025-06-01",
            "amount": -30000, "payee": "Save"})
        tr = client.post("/api/v1/transfers", headers=h, json={
            "debit_transaction_id": c.json()["id"],
            "credit_transaction_id": d.json()["id"]})
        assert tr.status_code == 201
        prog = client.get(f"/api/v1/savings-goals/{gid}/progress",
                          headers=h).json()
        assert prog["as_of"] == date.today().isoformat()
        assert prog["contributed_pence"] == 30000
        assert prog["pct"] == 25
        assert prog["monthly_need_pence"] is not None
        ledger = client.get(
            f"/api/v1/savings-goals/{gid}/contributions",
            headers=h).json()
        assert [row["transaction_id"] for row in ledger] == \
            [d.json()["id"]]


class TestProjections:
    """Issue #92 — projected completion, on-track, milestones."""

    def test_golden_projection(self, session: Session,
                               household: Household, pot: Account,
                               main: Account) -> None:
        """AC: £1,200 target, £300 saved, £100/mo → 9 months."""
        g = _goal(session, household, pot, target=120_000,
                  gtype=GoalType.general)
        # £300 contributed Jan–Mar = £100/mo (3 months elapsed)
        for i in range(3):
            _contribution(session, household, main, pot, 10_000,
                          date(2025, 1 + i, 15))
        rate = goals.average_monthly_rate(session, g, TODAY)
        assert rate == 10_000
        # £900 remaining ÷ £100/mo = 9 months → 2026-01-01
        assert goals.projected_completion(session, g, TODAY) == \
            date(2026, 1, 1)

    def test_missed_contribution_updates_projection(
            self, session: Session, household: Household,
            pot: Account, main: Account) -> None:
        """AC: projection reacts immediately to a new contribution."""
        g = _goal(session, household, pot, target=40_000,
                  gtype=GoalType.general)
        g.start_date = date(2025, 3, 10)
        _contribution(session, household, main, pot, 10_000,
                      date(2025, 3, 15))
        before = goals.projected_completion(session, g, TODAY)
        _contribution(session, household, main, pot, 10_000,
                      date(2025, 4, 5))
        after = goals.projected_completion(session, g, TODAY)
        assert after is not None and before is not None
        assert after < before  # more savings → sooner completion

    def test_zero_rate_projection_none(
            self, session: Session, household: Household,
            pot: Account) -> None:
        g = _goal(session, household, pot, target=40_000)
        assert goals.projected_completion(session, g, TODAY) is None

    def test_on_track_indicator(
            self, session: Session, household: Household,
            pot: Account, main: Account) -> None:
        g = _goal(session, household, pot, target=40_000,
                  target_date=date(2025, 5, 1))
        # required = £400 ÷ 4 months = £100/mo; saved £0 → off track
        assert goals.on_track(session, g, TODAY) is False
        _contribution(session, household, main, pot, 40_000,
                      date(2025, 4, 1))
        assert goals.on_track(session, g, TODAY) is True

    def test_undated_goal_track_none(
            self, session: Session, household: Household,
            pot: Account) -> None:
        g = _goal(session, household, pot, target=40_000)
        assert goals.on_track(session, g, TODAY) is None

    def test_milestone_trigger(self) -> None:
        """AC: a contribution crossing 25/50/75/100 fires the milestone
        Epic 15 consumes."""
        assert goals.newly_crossed(0, 30_000, 100_000) == [25]
        assert goals.newly_crossed(24_999, 50_000, 100_000) == [25, 50]
        assert goals.newly_crossed(70_000, 100_000, 100_000) == \
            [75, 100]
        assert goals.newly_crossed(10_000, 20_000, 100_000) == []

    def test_crossed_milestones_from_ledger(
            self, session: Session, household: Household,
            pot: Account, main: Account) -> None:
        g = _goal(session, household, pot, target=100_000)
        _contribution(session, household, main, pot, 60_000,
                      date(2025, 3, 1))
        assert goals.crossed_milestones(session, g) == [25, 50]

    def test_progress_exposes_projection(
            self, session: Session, household: Household,
            pot: Account, main: Account) -> None:
        g = _goal(session, household, pot, target=40_000)
        g.start_date = date(2025, 3, 10)
        _contribution(session, household, main, pot, 10_000,
                      date(2025, 4, 1))
        p = goals.progress(session, g, TODAY)
        assert p.average_rate_pence == 10_000
        assert p.projected_date == date(2025, 7, 1)
