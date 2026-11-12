"""Canonical freshness transitions and integration resource visibility."""

from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.core.scheduler import sync_tick
from app.domain import outbox
from app.persistence.models import (
    Account,
    Budget,
    BudgetPeriodType,
    EnergyReading,
    IntegrationEvent,
    SyncCursor,
    TransactionSource,
)


def test_scheduled_tick_emits_stale_once_per_last_success(session, household):
    now = datetime(2025, 4, 10, 12)
    account = Account(
        household_id=household.id,
        name="Synthetic bank account",
        type="joint",
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    session.add(account)
    session.flush()
    cursor = SyncCursor(
        account_id=account.id,
        source=TransactionSource.starling,
        last_success_at=now - timedelta(hours=9),
    )
    session.add(cursor)
    session.flush()
    for _ in range(2):
        sync_tick(
            session,
            client=object(),
            token_for=lambda *_: "unused",
            interval_seconds=4 * 3600,
            now=now,
        )
    events = list(
        session.scalars(select(IntegrationEvent).where(IntegrationEvent.event_type == "data.stale"))
    )
    assert len(events) == 1
    assert outbox.visible_payload(events[0], False)["status"] == "stale"
    cursor.last_success_at = now
    session.flush()
    sync_tick(
        session,
        client=object(),
        token_for=lambda *_: "unused",
        interval_seconds=4 * 3600,
        now=now + timedelta(hours=9),
    )
    events = list(
        session.scalars(select(IntegrationEvent).where(IntegrationEvent.event_type == "data.stale"))
    )
    assert len(events) == 2


def test_canonical_shared_resource_events_are_visible(session, household):
    budget = Budget(
        household_id=household.id,
        name="Synthetic budget",
        period_type=BudgetPeriodType.monthly,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 30),
    )
    reading = EnergyReading(
        household_id=household.id, date=date(2025, 4, 10), wh=1000, source="synthetic"
    )
    session.add_all([budget, reading])
    session.flush()
    for kind, entity_id in [
        ("budget", budget.id),
        ("energy_reading", reading.id),
        ("backup", None),
    ]:
        event = outbox.emit(
            session,
            household.id,
            kind + ".test",
            entity_kind=kind,
            entity_id=entity_id,
            payload={"status": "completed"},
        )
        assert outbox.event_visible(session, event, None)


def test_auto_pair_emits_real_goal_contribution_milestones(session, household):
    from app.domain import categorise
    from app.persistence.models import GoalType, SavingsGoal, Transaction

    source = Account(
        household_id=household.id,
        name="Source",
        type="joint",
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    target = Account(
        household_id=household.id,
        name="Savings",
        type="joint",
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    session.add_all([source, target])
    session.flush()
    goal = SavingsGoal(
        household_id=household.id,
        account_id=target.id,
        name="Synthetic goal",
        goal_type=GoalType.general,
        target_pence=10000,
        start_date=date(2025, 1, 1),
    )
    session.add(goal)
    session.add_all(
        [
            Transaction(
                account_id=source.id,
                date=date(2025, 4, 10),
                amount_pence=-10000,
                payee=target.name,
                source="starling",
            ),
            Transaction(
                account_id=target.id,
                date=date(2025, 4, 10),
                amount_pence=10000,
                payee=source.name,
                source="starling",
            ),
        ]
    )
    session.flush()
    assert categorise.auto_pair_transfers(session, household.id) == 1
    events = list(
        session.scalars(
            select(IntegrationEvent).where(IntegrationEvent.event_type == "goal.milestone")
        )
    )
    assert {event.payload["state"] for event in events} == {"25", "50", "75", "100"}
    assert categorise.auto_pair_transfers(session, household.id) == 0
    assert (
        len(
            list(
                session.scalars(
                    select(IntegrationEvent).where(IntegrationEvent.event_type == "goal.milestone")
                )
            )
        )
        == 4
    )


def test_private_budget_and_payslip_events_keep_source_owner(session, household):
    from app.persistence.models import (
        Employment,
        PayrollRun,
        Payslip,
        UserProfile,
    )

    owner = UserProfile(household_id=household.id, display_name="Private owner")
    session.add(owner)
    session.flush()
    budget = Budget(
        household_id=household.id,
        owner_profile_id=owner.id,
        name="Private budget",
        period_type=BudgetPeriodType.monthly,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 30),
    )
    employment = Employment(
        household_id=household.id,
        profile_id=owner.id,
        employer="Private employer",
        jurisdiction="england_wales",
        contracted_hours=37,
    )
    session.add_all([budget, employment])
    session.flush()
    run = PayrollRun(
        employment_id=employment.id,
        period_start=date(2025, 4, 1),
        period_end=date(2025, 4, 30),
        pay_date=date(2025, 4, 28),
        tax_year="2025/26",
        rule_set_version=1,
        gross_pence=10000,
        net_pence=9000,
    )
    session.add(run)
    session.flush()
    payslip = Payslip(
        household_id=household.id,
        payroll_run_id=run.id,
        original_name="synthetic.pdf",
        private_path="synthetic/no-file",
        sha256="a" * 64,
        size_bytes=1,
        media_type="application/pdf",
    )
    session.add(payslip)
    session.flush()
    for kind, entity_id in [("budget", budget.id), ("payslip", payslip.id)]:
        event = outbox.emit(
            session, household.id, kind + ".test", entity_kind=kind, entity_id=entity_id
        )
        assert not outbox.event_visible(session, event, None)
        assert not outbox.event_visible(session, event, owner.id + 1)
        assert outbox.event_visible(session, event, owner.id)


def test_viewer_latest_and_feed_both_hide_owned_private_events(session, household):
    from app.api.v1 import integration
    from app.persistence.models import Role, UserProfile

    viewer = UserProfile(household_id=household.id, display_name="Viewer", role=Role.viewer)
    session.add(viewer)
    session.flush()
    account = Account(
        household_id=household.id,
        name="Private viewer account",
        type="personal",
        owner_profile_id=viewer.id,
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    session.add(account)
    session.flush()
    visible = outbox.emit(session, household.id, "global.test")
    outbox.emit(session, household.id, "private.test", entity_kind="account", entity_id=account.id)
    initial = integration.events(household, session, viewer, latest=True)
    feed = integration.events(household, session, viewer)
    assert initial.next_cursor == visible.id
    assert [event.seq for event in feed.events] == [visible.id]


def test_shared_budget_alert_excludes_private_spending(session, household):
    from app.domain import notifications
    from app.persistence.models import (
        BudgetLine,
        BudgetLineSide,
        Category,
        NotificationKind,
        Transaction,
        UserProfile,
    )

    owner = UserProfile(household_id=household.id, display_name="Private owner")
    session.add(owner)
    session.flush()
    account = Account(
        household_id=household.id,
        name="Private budget spending",
        type="personal",
        owner_profile_id=owner.id,
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    category = Category(household_id=household.id, name="Synthetic category")
    budget = Budget(
        household_id=household.id,
        name="Shared budget",
        period_type=BudgetPeriodType.monthly,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 30),
    )
    session.add_all([account, category, budget])
    session.flush()
    session.add_all(
        [
            BudgetLine(
                budget_id=budget.id,
                category_id=category.id,
                side=BudgetLineSide.expense,
                original_pence=100,
            ),
            Transaction(
                account_id=account.id,
                date=date(2025, 4, 10),
                amount_pence=-500,
                payee="Private merchant",
                category_id=category.id,
            ),
        ]
    )
    session.flush()
    rule = notifications.create_rule(
        session,
        household.id,
        name="Shared overspend",
        kind=NotificationKind.budget_overspend,
        params={},
    )
    assert notifications.evaluate(session, rule, today=date(2025, 4, 10)) == 0
