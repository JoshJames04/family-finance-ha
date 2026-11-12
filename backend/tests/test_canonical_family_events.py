"""Canonical family events derive from persisted per-entity financial state."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.scheduler import notify_tick
from app.domain import outbox
from app.persistence.models import (
    Account,
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    Category,
    Confidence,
    Employment,
    EnergyConfig,
    EnergyReading,
    EnergyTariff,
    ExpectedMovement,
    ExpectedSource,
    Gift,
    IntegrationEvent,
    PayrollRun,
    Payslip,
    PayslipStatus,
    Role,
    SeasonalEvent,
    Transaction,
    TransactionSplit,
    TransactionStatus,
    UserProfile,
)

NOW = datetime(2026, 1, 10, 12)


def budget_fixture(session, household):
    account = Account(
        household_id=household.id, name="Synthetic", type="joint", opening_date=date(2026, 1, 1)
    )
    category = Category(household_id=household.id, name="Synthetic planned")
    session.add_all([account, category])
    session.flush()
    budget = Budget(
        household_id=household.id,
        name="Synthetic plan",
        period_type=BudgetPeriodType.monthly,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        status=BudgetStatus.approved,
    )
    session.add(budget)
    session.flush()
    session.add(
        BudgetLine(
            budget_id=budget.id,
            category_id=category.id,
            side=BudgetLineSide.expense,
            original_pence=10000,
        )
    )
    session.flush()
    return account, category, budget


def rows(session, prefix):
    return list(
        session.scalars(
            select(IntegrationEvent)
            .where(IntegrationEvent.event_type.like(prefix + "%"))
            .order_by(IntegrationEvent.id)
        )
    )


def tick(session, now=NOW):
    notify_tick(session, notifier=None, now=now)


def test_budget_state_uses_complete_signed_asof_ledger_and_deduplicates(session, household):
    account, category, budget = budget_fixture(session, household)
    tick(session)
    assert rows(session, "budget.") == []
    other = Category(household_id=household.id, name="Synthetic unplanned")
    session.add(other)
    session.flush()
    tx = Transaction(
        account_id=account.id,
        date=NOW.date(),
        amount_pence=-12000,
        payee="Synthetic",
        status=TransactionStatus.cleared,
    )
    session.add(tx)
    session.flush()
    session.add_all(
        [
            TransactionSplit(
                transaction_id=tx.id, category_id=category.id, amount_pence=-4000, position=0
            ),
            TransactionSplit(
                transaction_id=tx.id, category_id=other.id, amount_pence=-8000, position=1
            ),
        ]
    )
    # A future row is not spending so far.
    session.add(
        Transaction(
            account_id=account.id,
            date=NOW.date() + timedelta(days=1),
            amount_pence=-9000,
            payee="Future",
            status=TransactionStatus.cleared,
        )
    )
    tick(session)
    events = rows(session, "budget.")
    assert {event.event_type for event in events} == {
        "budget.usage",
        "budget.over",
        "budget.remaining_changed",
    }
    assert (
        next(event for event in events if event.event_type == "budget.usage").payload["spent_pence"]
        == 12000
    )
    assert all(event.entity_id == budget.id for event in events)
    tick(session)
    assert len(rows(session, "budget.")) == 3
    session.add(
        Transaction(
            account_id=account.id,
            date=NOW.date(),
            amount_pence=3000,
            payee="Refund",
            category_id=category.id,
            status=TransactionStatus.cleared,
        )
    )
    tick(session)
    assert len(rows(session, "budget.over")) == 1
    assert rows(session, "budget.remaining_changed")[-1].payload["remaining_pence"] == 1000
    assert rows(session, "budget.remaining_changed")[-1].payload["old_remaining_pence"] == -2000


def test_seasonal_variance_is_actual_and_commitment_scoped(session, household):
    event = SeasonalEvent(
        household_id=household.id, name="Synthetic", type="christmas", year=2026, budget_pence=10000
    )
    session.add(event)
    session.flush()
    gift = Gift(event_id=event.id, recipient="Synthetic", idea="Synthetic idea", planned_pence=3000)
    session.add(gift)
    session.flush()
    tick(session)
    gift.actual_pence = 4000
    tick(session)
    events = rows(session, "seasonal.variance")
    assert len(events) == 1
    assert events[0].entity_kind == "seasonal_event"
    assert events[0].payload["remaining_pence"] == 6000
    assert events[0].payload["remaining_after_commitments_pence"] == 6000
    assert outbox.visible_payload(events[0], False) == {
        "source": "seasonal",
        "status": "under",
        "date": "2026-01-10",
    }
    tick(session)
    assert len(rows(session, "seasonal.variance")) == 1


def test_energy_variance_recomputes_canonical_tariff_cost_and_reports_missing_days(
    session, household
):
    account, category, budget = budget_fixture(session, household)
    session.add_all(
        [
            EnergyConfig(household_id=household.id, budget_category_id=category.id),
            EnergyTariff(
                household_id=household.id,
                effective_from=date(2026, 1, 1),
                unit_rate_millipence=25000,
                standing_charge_pence=50,
            ),
        ]
    )
    reading = EnergyReading(
        household_id=household.id, date=NOW.date(), wh=10000, source="ha_statistics"
    )
    session.add(reading)
    session.flush()
    tick(session)
    reading.wh = 50000
    tick(session)
    events = rows(session, "energy.variance")
    assert len(events) == 1
    assert events[0].payload["estimate_pence"] == 1750
    assert events[0].payload["variance_pence"] == -8250
    assert events[0].payload["missing_days"] == 9
    assert events[0].payload["status"] == "estimate_incomplete"
    tick(session)
    assert len(rows(session, "energy.variance")) == 1


def payroll_fixture(session, household):
    profile = UserProfile(
        household_id=household.id, display_name="Synthetic member", role=Role.member
    )
    session.add(profile)
    session.flush()
    employment = Employment(
        household_id=household.id,
        profile_id=profile.id,
        employer="Synthetic",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
    )
    session.add(employment)
    session.flush()
    account = Account(
        household_id=household.id,
        owner_profile_id=profile.id,
        name="Synthetic personal",
        type="personal",
        opening_date=date(2026, 1, 1),
    )
    session.add(account)
    session.flush()
    return profile, employment, account


def test_payday_approaches_once_per_employment_occurrence_and_is_private(session, household):
    profile, employment, account = payroll_fixture(session, household)
    session.add(
        ExpectedMovement(
            household_id=household.id,
            account_id=account.id,
            expected_date=date(2026, 1, 13),
            amount_pence=10000,
            label="Synthetic salary",
            confidence=Confidence.estimated,
            source_type=ExpectedSource.employment,
            source_id=employment.id,
        )
    )
    session.flush()
    tick(session)
    tick(session, NOW + timedelta(days=1))
    events = rows(session, "payroll.approaching")
    assert len(events) == 1
    assert events[0].payload["expected_date"] == "2026-01-13"
    assert not outbox.event_visible(session, events[0], None)
    assert outbox.event_visible(session, events[0], profile.id)


def test_payslip_status_changes_do_not_disclose_artifact_contents(session, household):
    profile, employment, account = payroll_fixture(session, household)
    run = PayrollRun(
        employment_id=employment.id,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        pay_date=date(2026, 1, 30),
        tax_year="2025/26",
        rule_set_version=1,
        gross_pence=10000,
        net_pence=9000,
    )
    session.add(run)
    session.flush()
    slip = Payslip(
        household_id=household.id,
        payroll_run_id=run.id,
        original_name="Synthetic private name",
        private_path="/synthetic/private",
        sha256="a" * 64,
        size_bytes=1,
        media_type="application/pdf",
        status=PayslipStatus.needs_review,
    )
    session.add(slip)
    session.flush()
    tick(session)
    slip.status = PayslipStatus.confirmed
    tick(session)
    events = rows(session, "payslip.state_changed")
    assert len(events) == 1
    assert events[0].payload == {
        "source": "payslip",
        "old_status": "needs_review",
        "new_status": "confirmed",
        "status": "confirmed",
        "date": "2026-01-10",
    }
    assert not outbox.event_visible(session, events[0], None)
    assert outbox.event_visible(session, events[0], profile.id)
    tick(session)
    assert len(rows(session, "payslip.state_changed")) == 1


def test_canonical_state_and_events_roll_back_together(session, household):
    from app.domain.financial_events import evaluate_budgets

    account, category, budget = budget_fixture(session, household)
    tick(session)
    session.add(
        Transaction(
            account_id=account.id,
            date=NOW.date(),
            amount_pence=-12000,
            payee="Synthetic",
            status=TransactionStatus.cleared,
        )
    )
    evaluate_budgets(session, household.id, today=NOW.date())
    session.flush()
    assert len(rows(session, "budget.")) == 3
    session.rollback()
    assert rows(session, "budget.") == []
    session.add(
        Transaction(
            account_id=account.id,
            date=NOW.date(),
            amount_pence=-12000,
            payee="Retry",
            status=TransactionStatus.cleared,
        )
    )
    evaluate_budgets(session, household.id, today=NOW.date())
    session.flush()
    assert len(rows(session, "budget.")) == 3


def test_shared_budget_and_energy_never_aggregate_private_account_or_plan(session, household):
    account, category, budget = budget_fixture(session, household)
    profile, employment, private_account = payroll_fixture(session, household)
    private_budget = Budget(
        household_id=household.id,
        owner_profile_id=profile.id,
        name="Private plan",
        period_type=BudgetPeriodType.monthly,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        status=BudgetStatus.approved,
    )
    session.add(private_budget)
    session.flush()
    session.add(
        BudgetLine(
            budget_id=private_budget.id,
            category_id=category.id,
            side=BudgetLineSide.expense,
            original_pence=999999,
        )
    )
    session.add_all(
        [
            EnergyConfig(household_id=household.id, budget_category_id=category.id),
            EnergyTariff(
                household_id=household.id,
                effective_from=date(2026, 1, 1),
                unit_rate_millipence=25000,
                standing_charge_pence=50,
            ),
        ]
    )
    tick(session)
    session.add(
        Transaction(
            account_id=private_account.id,
            date=NOW.date(),
            amount_pence=-12000,
            payee="Private",
            category_id=category.id,
            status=TransactionStatus.cleared,
        )
    )
    session.add(
        EnergyReading(household_id=household.id, date=NOW.date(), wh=10000, source="ha_statistics")
    )
    tick(session)
    assert all(event.entity_id == private_budget.id for event in rows(session, "budget."))
    assert rows(session, "energy.variance")[0].payload["budget_pence"] == 10000


def test_manual_backup_failure_is_durable_redacted_and_once_per_streak(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.settings import Settings
    from app.main import create_app
    from app.persistence import backup
    from app.persistence.models import Household

    app = create_app(
        Settings.load(
            {
                "FF_DATA_DIR": str(tmp_path),
                "FF_ADMIN_TOKEN": "synthetic-token",
                "FF_SCHEDULER_TICK_SECONDS": "0",
                "FF_BACKUP_INTERVAL_HOURS": "0",
            }
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        with Session(app.state.engine) as db:
            hh = Household(name="Synthetic")
            db.add(hh)
            db.commit()
            hid = hh.id

        def fail(*args, **kwargs):
            raise OSError("synthetic private path and secret=not-for-event")

        monkeypatch.setattr(backup, "create_snapshot", fail)
        for _ in range(2):
            response = client.post(
                "/api/v1/admin/backups",
                headers={"X-Admin-Token": "synthetic-token", "X-Household-Id": str(hid)},
            )
            assert response.status_code == 500
        with Session(app.state.engine) as db:
            events = rows(db, "backup.failed")
            assert len(events) == 1
            assert events[0].payload["status"] == "failed"
            assert "private" not in str(events[0].payload)
            assert "secret" not in str(events[0].payload)
            assert outbox.visible_payload(events[0], True) == outbox.visible_payload(
                events[0], False
            )


def test_successful_sync_after_failure_emits_one_canonical_recovery(session, household):
    from app.domain import sync
    from app.persistence.models import SyncStatus, TransactionSource

    account, category, budget = budget_fixture(session, household)
    cursor = sync.cursor_for(session, account.id, TransactionSource.starling)
    cursor.status = SyncStatus.failed
    session.commit()
    sync.sync_account(session, account, TransactionSource.starling, lambda cursor: iter([]))
    assert len(rows(session, "sync.recovered")) == 1
    sync.sync_account(session, account, TransactionSource.starling, lambda cursor: iter([]))
    assert len(rows(session, "sync.recovered")) == 1


def test_detailed_sync_failure_never_exports_raw_provider_error(session, household):
    from app.domain import sync
    from app.persistence.models import TransactionSource

    account, category, budget = budget_fixture(session, household)

    def failing(cursor):
        raise sync.SyncError("private provider response must not enter event payloads")
        yield

    sync.sync_account(session, account, TransactionSource.starling, failing)
    event = rows(session, "sync.failed")[0]
    assert "private provider response" not in str(outbox.visible_payload(event, True))


def test_real_transaction_boundary_emits_without_scheduler_or_notification_rule(tmp_path):
    import calendar

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.settings import Settings
    from app.main import create_app

    today = date.today()
    app = create_app(
        Settings.load(
            {
                "FF_DATA_DIR": str(tmp_path),
                "FF_SCHEDULER_TICK_SECONDS": "0",
                "FF_BACKUP_INTERVAL_HOURS": "0",
            }
        )
    )
    with TestClient(app) as client:
        hid = client.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
        client.headers["X-Household-Id"] = str(hid)
        admin = client.post(
            "/api/v1/profiles", json={"display_name": "Synthetic admin", "role": "admin"}
        ).json()
        client.headers["X-Profile-Id"] = str(admin["id"])
        account = client.post(
            "/api/v1/accounts",
            json={
                "name": "Synthetic",
                "type": "joint",
                "opening_balance": 0,
                "opening_date": today.isoformat(),
            },
        ).json()
        category = client.post("/api/v1/categories", json={"name": "Synthetic"}).json()
        budget = client.post(
            "/api/v1/budgets",
            json={
                "name": "Synthetic",
                "period_type": "monthly",
                "start_date": today.replace(day=1).isoformat(),
                "end_date": today.replace(
                    day=calendar.monthrange(today.year, today.month)[1]
                ).isoformat(),
            },
        ).json()
        assert (
            client.post(
                f"/api/v1/budgets/{budget['id']}/lines",
                json={"category_id": category["id"], "side": "expense", "amount": 10000},
            ).status_code
            == 201
        )
        assert client.post(f"/api/v1/budgets/{budget['id']}/approve").status_code == 200
        transaction = client.post(
            "/api/v1/transactions",
            json={
                "account_id": account["id"],
                "category_id": category["id"],
                "date": today.isoformat(),
                "amount": -12000,
                "payee": "Synthetic",
            },
        )
        assert transaction.status_code == 201, transaction.text
        with Session(app.state.engine) as db:
            assert len(rows(db, "budget.over")) == 1
            assert rows(db, "budget.usage")[0].payload["spent_pence"] == 12000
        assert (
            client.patch(
                f"/api/v1/transactions/{transaction.json()['id']}",
                json={"category_id": category["id"]},
            ).status_code
            == 200
        )
        with Session(app.state.engine) as db:
            assert len(rows(db, "budget.over")) == 1
            assert len(rows(db, "budget.usage")) == 1


def test_backup_recovers_then_a_new_failure_emits_again(session, household):
    from app.domain.financial_events import record_backup_status

    for succeeded in [False, False, True, False]:
        record_backup_status(session, household.id, succeeded=succeeded, today=NOW.date())
        session.flush()
    assert len(rows(session, "backup.failed")) == 2


def test_energy_projection_counts_known_zero_days_but_not_missing_days(session, household):
    from app.domain.energy import month_report

    session.add(
        EnergyTariff(
            household_id=household.id,
            effective_from=date(2026, 1, 1),
            unit_rate_millipence=25000,
            standing_charge_pence=50,
        )
    )
    session.add_all(
        [
            EnergyReading(
                household_id=household.id, date=date(2026, 1, 1), wh=10000, source="ha_statistics"
            ),
            EnergyReading(
                household_id=household.id, date=date(2026, 1, 2), wh=0, source="ha_statistics"
            ),
        ]
    )
    session.flush()
    report = month_report(session, household.id, date(2026, 1, 3))
    assert report.estimate.missing_days == 1
    assert report.avg_daily_wh == 5000
    assert report.projected_total_pence == 5300


def test_shared_energy_report_does_not_disclose_private_budget_plan(session, household, tmp_path):
    from fastapi.testclient import TestClient

    from app.api.deps import get_session
    from app.core.settings import Settings
    from app.main import create_app

    account, category, budget = budget_fixture(session, household)
    profile, employment, private_account = payroll_fixture(session, household)
    private = Budget(
        household_id=household.id,
        owner_profile_id=profile.id,
        name="Private",
        period_type=BudgetPeriodType.monthly,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        status=BudgetStatus.approved,
    )
    session.add(private)
    session.flush()
    session.add_all(
        [
            BudgetLine(
                budget_id=private.id,
                category_id=category.id,
                side=BudgetLineSide.expense,
                original_pence=999999,
            ),
            EnergyConfig(household_id=household.id, budget_category_id=category.id),
            EnergyTariff(
                household_id=household.id,
                effective_from=date(2026, 1, 1),
                unit_rate_millipence=25000,
                standing_charge_pence=50,
            ),
        ]
    )
    session.flush()
    app = create_app(
        Settings.load(
            {"FF_DATA_DIR": str(tmp_path), "FF_INGRESS": "false", "FF_SCHEDULER_TICK_SECONDS": "0"}
        )
    )
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/energy/report?as_of=2026-01-10", headers={"X-Household-Id": str(household.id)}
        )
        assert response.status_code == 200, response.text
        assert response.json()["budget_pence"] == 10000


def test_remaining_changed_requires_a_real_remaining_value_change(session, household):
    account, category, budget = budget_fixture(session, household)
    tick(session)
    budget.lines[0].revised_pence = 11000
    session.add(
        Transaction(
            account_id=account.id,
            date=NOW.date(),
            amount_pence=-1000,
            payee="Synthetic",
            category_id=category.id,
            status=TransactionStatus.cleared,
        )
    )
    tick(session)
    assert len(rows(session, "budget.usage")) == 1
    assert rows(session, "budget.remaining_changed") == []
