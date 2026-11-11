"""Read-only review regression probes; copied into backend/tests to use fixtures."""

import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.domain import payroll, payrun, schedule, tax
from app.persistence.models import (
    Account,
    Employment,
    ExpectedMovement,
    ExpectedSource,
    PayrollComponentKind,
    UserProfile,
)

TODAY = date(2025, 4, 10)
RULE = json.dumps({"kind": "fixed_day", "day": 28, "adjust": "none"})


def employment(session, household, start=date(2025, 1, 1)):
    p = UserProfile(household_id=household.id, display_name="A", role="admin")
    session.add(p)
    session.flush()
    a = Account(
        household_id=household.id,
        owner_profile_id=p.id,
        name="A",
        type="personal",
        opening_date=date(2025, 1, 1),
    )
    session.add(a)
    session.flush()
    e = Employment(
        household_id=household.id,
        profile_id=p.id,
        employer="Acme",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
        start_date=start,
        payday_rule=RULE,
        paid_into_account_id=a.id,
    )
    session.add(e)
    session.flush()
    payroll.add_salary_revision(session, e, start, 3_600_000)
    payroll.add_tax_code_revision(session, e, start, "1257L")
    return e, a, p


def projected(session):
    return list(
        session.scalars(
            select(ExpectedMovement).where(
                ExpectedMovement.source_type == ExpectedSource.employment
            )
        )
    )


def test_no_run_estimate_uses_one_month_tax_allowance(session, household):
    e, _, _ = employment(session, household)
    rules = tax.rule_set(session, tax.tax_year_for(TODAY), e.jurisdiction)
    correct = tax.compute_period(
        gross_pence=300000, tax_code="1257L", params=json.loads(rules.params), period=1
    ).net_pence
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    actual = projected(session)[0].amount_pence
    assert actual == correct, f"projected net={actual}, standard monthly net={correct}"


def test_future_start_with_future_initial_revisions_projects_after_start(session, household):
    e, _, _ = employment(session, household, start=date(2025, 5, 1))
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=90)
    dates = [r.expected_date.isoformat() for r in projected(session)]
    assert dates == ["2025-05-28", "2025-06-28"], f"future-start payday projections={dates}"


def test_run_only_override_does_not_carry_into_later_paydays(session, household):
    e, _, _ = employment(session, household)
    run = payrun.generate(
        session, e, start=date(2025, 3, 1), end=date(2025, 3, 31), pay_date=date(2025, 3, 28)
    )
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    baseline = projected(session)[0].amount_pence
    payrun.apply_override(
        session,
        run,
        kind=PayrollComponentKind.bonus,
        delta_pence=50000,
        reason="one off",
        actor="review",
    )
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    actual = projected(session)[0].amount_pence
    assert actual == baseline, f"one-off override leaked: projected={actual}, baseline={baseline}"


def test_paid_into_account_change_moves_unmatched_future_expectations(session, household):
    e, old, _ = employment(session, household)
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    new = Account(household_id=household.id, name="New", type="personal", opening_date=TODAY)
    session.add(new)
    session.flush()
    e.paid_into_account_id = new.id
    session.flush()
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    rows = projected(session)
    assert len(rows) == 1
    assert rows[0].account_id == new.id
    assert all(r.account_id != old.id for r in rows)


def test_effective_revisions_and_employment_dates(session, household):
    e, _, _ = employment(session, household, start=date(2025, 5, 1))
    e.end_date = date(2025, 6, 30)
    e.deduction_mode = "gross_only"
    payroll.add_salary_revision(session, e, date(2025, 6, 1), 4_800_000)
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=120)
    assert [(m.expected_date, m.amount_pence) for m in projected(session)] == [
        (date(2025, 5, 28), 300000),
        (date(2025, 6, 28), 400000),
    ]


def test_revision_endpoints_refresh_future_income(session, household):
    from app.api import schemas
    from app.api.v1 import employments as api

    today = date.today()
    e, _, _ = employment(session, household, start=today.replace(year=today.year - 1))
    e.deduction_mode = "gross_only"
    schedule.rematerialize_employment(session, e)
    rev = api.add_salary_revision(
        e.id,
        schemas.SalaryRevisionIn(effective_date=today, annual_salary=4800000),
        household,
        session,
        "test",
    )
    assert {m.amount_pence for m in projected(session)} == {400000}
    api.patch_salary_revision(
        e.id, rev.id, schemas.SalaryRevisionPatch(annual_salary=6000000), household, session, "test"
    )
    assert {m.amount_pence for m in projected(session)} == {500000}
    api.delete_salary_revision(e.id, rev.id, household, session, "test")
    assert {m.amount_pence for m in projected(session)} == {300000}


def test_delete_employment_removes_future_income(session, household):
    from app.api.v1 import employments as api

    e, _, _ = employment(session, household)
    e.deduction_mode = "gross_only"
    schedule.rematerialize_employment(session, e)
    assert projected(session)
    api.delete_employment(e.id, household, session, "test")
    session.flush()
    assert projected(session) == []


def test_background_repair_advances_horizon_and_cleans_inactive(session, household):
    e, _, _ = employment(session, household)
    e.deduction_mode = "gross_only"
    schedule.rematerialize_employments(session, today=TODAY, horizon_days=35)
    assert [m.expected_date for m in projected(session)] == [date(2025, 4, 28)]
    schedule.rematerialize_employments(session, today=date(2025, 4, 29), horizon_days=35)
    assert date(2025, 5, 28) in [m.expected_date for m in projected(session)]
    e.is_active = False
    schedule.rematerialize_employments(session, today=TODAY, horizon_days=35)
    assert projected(session) == []


def test_scenario_and_reminder_include_projected_payday(session, household):
    from app.domain import notifications, scenario
    from app.persistence.models import NotificationKind

    e, a, _ = employment(session, household)
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    assert scenario._payday_dates(session, a.id, TODAY, date(2025, 5, 1)) == [date(2025, 4, 28)]
    rule = notifications.create_rule(
        session,
        household.id,
        name="Salary",
        kind=NotificationKind.payday_reminder,
        params={"days_before": 1},
    )
    assert notifications.evaluate(session, rule, today=date(2025, 4, 27)) == 1
    assert notifications.evaluate(session, rule, today=date(2025, 4, 27)) == 0


def test_actual_net_wins_even_after_reopening_and_zero_correction(session, household):
    from app.persistence.models import ExpectedMovement

    e, _, _ = employment(session, household)
    run = payrun.generate(
        session, e, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    actuals = {c.kind: 0 for c in run.components}
    payrun.reconcile(session, run, actuals)
    payrun.reopen(session, run)
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    row = session.scalar(
        select(ExpectedMovement).where(ExpectedMovement.source_type == ExpectedSource.payroll_run)
    )
    assert row.amount_pence == 0
    payrun.reconcile(session, run, actuals)
    correction = payrun.correct(session, run)
    assert correction.net_pence == 0
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    schedule.rematerialize_payroll_run(session, correction, today=TODAY)
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    schedule.rematerialize_payroll_run(session, correction, today=TODAY)
    rows = list(session.scalars(select(ExpectedMovement)))
    assert len(rows) == 1
    assert rows[0].source_id == correction.id
    assert rows[0].amount_pence == 0


def test_dismissed_projection_survives_rebuild(session, household):
    from datetime import datetime

    e, _, _ = employment(session, household)
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    movement = projected(session)[0]
    movement.dismissed_at = datetime(2025, 4, 10)
    session.flush()
    movement_id = movement.id
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    rows = projected(session)
    assert len(rows) == 1
    assert rows[0].id == movement_id
    assert rows[0].dismissed_at == datetime(2025, 4, 10)


def test_missing_tax_prerequisite_does_not_copy_earlier_bonus_run(session, household):
    from sqlalchemy import delete

    from app.persistence.models import TaxCodeRevision

    e, _, _ = employment(session, household)
    payrun.generate(
        session,
        e,
        start=date(2025, 3, 1),
        end=date(2025, 3, 31),
        pay_date=date(2025, 3, 28),
        bonus_pence=50000,
    )
    session.execute(delete(TaxCodeRevision).where(TaxCodeRevision.employment_id == e.id))
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    assert projected(session) == []


def test_startup_repairs_existing_install_without_scheduler(session, household, tmp_path):
    from fastapi.testclient import TestClient

    from app.core.settings import Settings
    from app.domain import scenario
    from app.main import create_app
    from app.persistence.models import ScenarioOpKind

    e, account, _ = employment(session, household)
    e.deduction_mode = "gross_only"
    session.commit()
    settings = Settings.load(
        {
            "FF_DATA_DIR": str(tmp_path / "app"),
            "FF_DATABASE_URL": str(session.get_bind().url),
            "FF_SCHEDULER_TICK_SECONDS": "0",
        }
    )
    with TestClient(create_app(settings)):
        session.expire_all()
        assert projected(session)
    saved = scenario.create_scenario(session, household.id, "Saved salary plan")
    scenario.add_op(
        session, saved, kind=ScenarioOpKind.remove, expected_movement_id=projected(session)[0].id
    )
    original_ids = {m.expected_date: m.id for m in projected(session)}
    before = scenario.run(session, saved, account, days=365)
    session.commit()
    for _ in range(2):
        with TestClient(create_app(settings)):
            session.expire_all()
            assert {m.expected_date: m.id for m in projected(session)} == original_ids
            assert scenario.run(session, saved, account, days=365).projection == before.projection


def test_employment_replacement_removes_old_future_rows(session, household):
    from datetime import timedelta

    from app.api import schemas
    from app.api.v1 import employments as api

    e, a, p = employment(session, household)
    e.deduction_mode = "gross_only"
    schedule.rematerialize_employment(session, e)
    new_start = date.today() + timedelta(days=40)
    api.create_employment(
        schemas.EmploymentIn(
            profile_id=p.id,
            employer="New",
            contracted_hours=37.5,
            start_date=new_start,
            deduction_mode="gross_only",
            payday_rule=RULE,
            paid_into_account_id=a.id,
            initial_salary=4800000,
            replaces_employment_id=e.id,
            replacement_confirmed=True,
        ),
        household,
        session,
        "test",
    )
    old_rows = [m for m in projected(session) if m.source_id == e.id]
    assert all(m.expected_date < new_start for m in old_rows)
    assert any(m.source_id != e.id for m in projected(session))


def test_tax_code_and_deduction_changes_refresh_estimates(session, household, monkeypatch):
    from app.api import schemas
    from app.api.v1 import employments as api
    from app.persistence.models import PensionMode

    class Clock(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(api, "date", Clock)
    # schedule's default uses its own clock.
    monkeypatch.setattr(schedule, "date", Clock)
    e, _, _ = employment(session, household)
    schedule.rematerialize_employment(session, e, horizon_days=35)
    ordinary_net = projected(session)[0].amount_pence
    api.add_tax_code_revision(
        e.id,
        schemas.TaxCodeRevisionIn(effective_date=TODAY, tax_code="BR"),
        household,
        session,
        "test",
    )
    baseline = projected(session)[0].amount_pence
    assert baseline < ordinary_net
    api.patch_employment(
        e.id,
        schemas.EmploymentPatch(
            pension_mode=PensionMode.relief_at_source, pension_fixed_pence=10000
        ),
        household,
        session,
        "test",
    )
    assert projected(session)[0].amount_pence == baseline - 10000


def test_adding_first_salary_repairs_missing_projection(session, household):
    from sqlalchemy import delete

    from app.api import schemas
    from app.api.v1 import employments as api
    from app.persistence.models import SalaryRevision

    e, _, _ = employment(session, household)
    e.deduction_mode = "gross_only"
    session.execute(delete(SalaryRevision).where(SalaryRevision.employment_id == e.id))
    schedule.rematerialize_employment(session, e)
    assert projected(session) == []
    api.add_salary_revision(
        e.id,
        schemas.SalaryRevisionIn(effective_date=date.today(), annual_salary=4800000),
        household,
        session,
        "test",
    )
    assert projected(session)
    assert {m.amount_pence for m in projected(session)} == {400000}


def test_unchanged_scheduler_ticks_keep_forecast_ids(session, household, tmp_path, monkeypatch):
    import threading

    from fastapi.testclient import TestClient

    from app.core import scheduler
    from app.core.settings import Settings
    from app.main import create_app

    e, _, _ = employment(session, household)
    e.deduction_mode = "gross_only"
    session.commit()
    second_tick = threading.Event()
    row_ids = []

    def tick(s, **kwargs):
        row_ids.append([m.id for m in projected(s)])
        if len(row_ids) >= 2:
            second_tick.set()

    monkeypatch.setattr(scheduler, "sync_tick", tick)
    monkeypatch.setattr(scheduler, "notify_tick", lambda *args, **kwargs: None)
    settings = Settings.load(
        {
            "FF_DATA_DIR": str(tmp_path / "app"),
            "FF_DATABASE_URL": str(session.get_bind().url),
            "FF_SCHEDULER_TICK_SECONDS": "0.02",
        }
    )
    with TestClient(create_app(settings)):
        assert second_tick.wait(timeout=10)
    assert row_ids[0]
    assert row_ids[1] == row_ids[0]


def test_real_run_reuses_matched_employment_projection(session, household):
    from app.persistence.models import ExpectedMatch, Transaction

    e, a, _ = employment(session, household)
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    movement = projected(session)[0]
    movement_id = movement.id
    txn = Transaction(
        account_id=a.id,
        date=movement.expected_date,
        amount_pence=movement.amount_pence,
        payee="Acme",
    )
    session.add(txn)
    session.flush()
    match = ExpectedMatch(
        movement_id=movement.id,
        transaction_id=txn.id,
        matched_pence=movement.amount_pence,
        manual=True,
    )
    session.add(match)
    session.flush()
    run = payrun.generate(
        session, e, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    payrun.reconcile(session, run, {})
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    rows = list(session.scalars(select(ExpectedMovement)))
    assert len(rows) == 1
    assert rows[0].id == movement_id
    assert rows[0].source_type == ExpectedSource.payroll_run
    assert rows[0].source_id == run.id
    assert rows[0].amount_pence == run.net_pence
    assert match.movement_id == rows[0].id
    assert match.matched_pence == txn.amount_pence

    correction = payrun.correct(session, run)
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    schedule.rematerialize_payroll_run(session, correction, today=TODAY)
    schedule.rematerialize_payroll_run(session, correction, today=TODAY)
    rows = list(session.scalars(select(ExpectedMovement)))
    assert len(rows) == 1
    assert rows[0].id == movement_id
    assert rows[0].source_id == correction.id
    assert match.movement_id == movement_id


def test_dismissal_survives_real_run_supersession(session, household):
    from datetime import datetime

    e, _, _ = employment(session, household)
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    movement = projected(session)[0]
    movement.dismissed_at = datetime(2025, 4, 10)
    session.flush()
    run = payrun.generate(
        session, e, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    schedule.rematerialize_payroll_run(session, run, today=TODAY)
    rows = list(session.scalars(select(ExpectedMovement)))
    assert len(rows) == 1
    assert rows[0].dismissed_at == datetime(2025, 4, 10)


def test_salary_scenario_references_survive_rebuild_and_do_not_retarget_accounts(
    session, household
):
    from datetime import timedelta

    from app.domain import scenario
    from app.persistence.models import ScenarioOpKind

    e, account, _ = employment(session, household)
    e.deduction_mode = "gross_only"
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=60)
    original = {m.expected_date: m.id for m in projected(session)}
    rows = projected(session)
    saved = scenario.create_scenario(session, household.id, "Salary changes")
    removal = scenario.add_op(
        session, saved, kind=ScenarioOpKind.remove, expected_movement_id=rows[0].id
    )
    shift = scenario.add_op(
        session,
        saved,
        kind=ScenarioOpKind.shift,
        expected_movement_id=rows[1].id,
        expected_date=date(2025, 6, 5),
    )
    # Prevent SQLite's incidental reuse of the most recently deleted ID from
    # hiding identity churn in a tiny synthetic database.
    schedule.add_manual(
        session,
        household.id,
        account_id=None,
        expected_date=TODAY,
        amount_pence=1,
        label="Other plan",
    )
    before = scenario.run(session, saved, account, days=60, from_date=TODAY)
    for day in (TODAY, TODAY + timedelta(days=1), TODAY + timedelta(days=2)):
        schedule.rematerialize_employments(session, today=day, horizon_days=60)
        assert {m.expected_date: m.id for m in projected(session)} == original
        after = scenario.run(session, saved, account, days=60, from_date=TODAY)
        assert after.projection == before.projection
        assert after.baseline == before.baseline
    destination = Account(
        household_id=household.id, name="Replacement account", type="personal", opening_date=TODAY
    )
    session.add(destination)
    session.flush()
    e.paid_into_account_id = destination.id
    schedule.rematerialize_employments(session, today=TODAY, horizon_days=60)
    assert removal.expected_movement_id is None
    assert shift.expected_movement_id is None
    changed = scenario.run(session, saved, destination, days=60, from_date=TODAY)
    assert changed.projection == changed.baseline
    assert {m.account_id for m in projected(session)} == {destination.id}
    changed_ids = {m.expected_date: m.id for m in projected(session)}
    schedule.rematerialize_employments(session, today=TODAY, horizon_days=60)
    assert {m.expected_date: m.id for m in projected(session)} == changed_ids


def test_salaried_nightly_rule_does_not_repeat_monthly_pay_daily(session, household):
    e, _, _ = employment(session, household)
    e.deduction_mode = "gross_only"
    e.payday_rule = json.dumps({"kind": "nightly", "adjust": "none"})
    schedule.rematerialize_employment(session, e, today=TODAY, horizon_days=35)
    assert projected(session) == []
