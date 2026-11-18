"""Automatic forecasts use the same guarded calculation as reviewed rebuilds."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain import payroll
from app.persistence.models import Employment, PayrollRun, PayrollRunStatus, UserProfile


@pytest.fixture
def employment(session, household):
    owner = UserProfile(household_id=household.id, display_name="Synthetic owner")
    session.add(owner)
    session.flush()
    row = Employment(
        household_id=household.id,
        profile_id=owner.id,
        employer="Synthetic employer",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
        start_date=date(2025, 4, 1),
        salary_policy="equal_monthly",
        payday_rule='{"kind":"fixed_day","day":28}',
    )
    session.add(row)
    session.flush()
    payroll.add_salary_revision(session, row, date(2025, 4, 1), 3600000)
    payroll.add_tax_code_revision(session, row, date(2025, 4, 1), "1257L M1")
    return row


def test_refresh_generates_future_payroll_once(session, employment):
    from app.domain.payroll_automation import refresh

    first = refresh(session, employment, today=date(2025, 4, 1))
    assert first["generated"] == 21
    assert not first["issues"]
    again = refresh(session, employment, today=date(2025, 4, 1))
    assert again["generated"] == again["updated"] == 0
    rows = list(session.scalars(select(PayrollRun)))
    assert len(rows) == 21
    assert all(r.status is PayrollRunStatus.forecast for r in rows)
    assert all(c.actual_pence is None for r in rows for c in r.components)
    assert rows[0].gross_pence == 300000


def test_source_change_updates_forecast_but_preserves_entered_actuals(session, employment):
    from app.domain.payroll_automation import refresh

    refresh(session, employment, today=date(2025, 4, 1))
    rows = list(session.scalars(select(PayrollRun).order_by(PayrollRun.pay_date)))
    rows[0].components[0].actual_pence = 300000
    protected = rows[0].gross_pence
    payroll.add_salary_revision(session, employment, date(2025, 5, 1), 4800000)
    result = refresh(session, employment, today=date(2025, 4, 1))
    assert result["updated"] == 20
    assert rows[0].gross_pence == protected
    assert rows[1].gross_pence == 400000
    assert not rows[1].forecast_stale
    assert result["issues"]


def test_incomplete_setup_does_not_undo_salary_edit(session, employment):
    from app.domain.payroll_automation import refresh

    employment.payday_rule = None
    result = refresh(session, employment, today=date(2025, 4, 1))
    assert result["generated"] == 0
    assert result["issues"]
    session.flush()
    assert payroll.salary_at(session, employment.id, date(2025, 4, 1)) == 3600000


def test_overtime_approval_and_edit_refresh_without_manual_apply(
    session, household, employment, monkeypatch
):
    from app.api import schemas
    from app.api.v1 import employments
    from app.domain import overtime, payroll_automation

    class Clock(date):
        @classmethod
        def today(cls):
            return date(2025, 4, 1)

    monkeypatch.setattr(payroll_automation, "date", Clock)
    payroll_automation.refresh(session, employment)
    run = session.scalar(select(PayrollRun).order_by(PayrollRun.pay_date))
    original_net = run.net_pence
    entry = overtime.create_entry(
        session, employment, work_date=date(2025, 4, 10), hours=Decimal("4")
    )
    overtime.submit(entry)
    employments.overtime_transition(
        employment.id, entry.id, "approve", household, session, "Synthetic"
    )
    assert run.gross_pence == 300000 + entry.pay_pence
    assert run.net_pence > original_net
    assert not run.forecast_stale
    employments.patch_overtime(
        employment.id, entry.id, schemas.OvertimePatch(hours="2"), household, session, "Synthetic"
    )
    assert run.gross_pence == 300000  # Changed work needs approval again.
    assert run.net_pence == original_net


def test_manual_override_survives_automatic_recalculation(session, employment):
    from app.domain import payroll_automation, payrun
    from app.persistence.models import PayrollComponentKind

    payroll_automation.refresh(session, employment, today=date(2025, 4, 1))
    run = session.scalar(select(PayrollRun).order_by(PayrollRun.pay_date))
    payrun.apply_override(
        session,
        run,
        kind=PayrollComponentKind.allowance,
        delta_pence=100,
        reason="Synthetic manual correction",
        actor="Synthetic",
    )
    baseline = (
        run.gross_pence,
        run.net_pence,
        [(c.kind, c.forecast_pence) for c in run.components],
    )
    payroll.invalidate_forecasts(session, employment.id)
    payroll_automation.refresh(session, employment, today=date(2025, 4, 1))
    assert baseline == (
        run.gross_pence,
        run.net_pence,
        [(c.kind, c.forecast_pence) for c in run.components],
    )


def test_missing_cumulative_history_is_reported_without_guessing(session, employment):
    from app.domain import payroll_automation
    from app.persistence.models import Account

    account = Account(
        household_id=employment.household_id,
        name="Synthetic pay account",
        type="personal",
        opening_date=date(2025, 4, 1),
    )
    session.add(account)
    session.flush()
    employment.paid_into_account_id = account.id
    employment.tax_code_revisions[0].tax_code = "1257L"
    result = payroll_automation.refresh(session, employment, today=date(2025, 8, 1))
    assert result["generated"] == 0
    assert result["issues"]
    assert not list(session.scalars(select(PayrollRun)))
    from app.persistence.models import ExpectedMovement

    assert not list(session.scalars(select(ExpectedMovement)))


def test_weekly_forecasts_do_not_duplicate_shifted_paydays(session, employment):
    from app.domain import payroll_automation

    employment.payday_rule = '{"kind":"weekly","weekday":4,"adjust":"previous"}'
    employment.deduction_mode = "gross_only"
    first = payroll_automation.refresh(session, employment, today=date(2025, 4, 1))
    assert first["generated"] > 50
    again = payroll_automation.refresh(session, employment, today=date(2025, 4, 1))
    assert again["generated"] == 0


def test_refresh_api_requires_the_payroll_owner(tmp_path):
    from fastapi.testclient import TestClient

    from app.core.settings import Settings
    from app.main import create_app
    from tests.test_payrun import TestApi

    with TestClient(
        create_app(Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"}))
    ) as client:
        headers, emp_id = TestApi()._setup(client)
        owner_id = client.get(f"/api/v1/employments/{emp_id}", headers=headers).json()["profile_id"]
        other = client.post(
            "/api/v1/profiles", headers=headers, json={"display_name": "Other member"}
        ).json()
        path = f"/api/v1/employments/{emp_id}/refresh-payroll"
        denied = client.post(path, headers=headers | {"X-Profile-Id": str(other["id"])})
        assert denied.status_code in (403, 404)
        allowed = client.post(path, headers=headers | {"X-Profile-Id": str(owner_id)})
        assert allowed.status_code == 200
        assert allowed.json()["generated"] == 0
        assert allowed.json()["issues"]  # Missing payday, rather than guessed setup.


@pytest.mark.parametrize("inactive", [False, True])
def test_ending_or_disabling_employer_withdraws_unprotected_future_payroll(
    session, employment, inactive
):
    from app.domain.payroll_automation import refresh

    refresh(session, employment, today=date(2025, 4, 1))
    runs = list(session.scalars(select(PayrollRun).order_by(PayrollRun.pay_date)))
    runs[0].components[0].actual_pence = 300000
    if inactive:
        employment.is_active = False
    else:
        employment.end_date = date(2025, 5, 15)
    payroll.invalidate_forecasts(session, employment.id)
    refresh(session, employment, today=date(2025, 4, 1))
    assert runs[0].status is PayrollRunStatus.forecast  # Independent evidence protected.
    assert all(r.status is PayrollRunStatus.corrected for r in runs[2:])
    if not inactive:
        assert runs[1].period_end == date(2025, 5, 15)
        assert runs[1].gross_pence == 145161  # 15 of 31 days at the monthly salary.


def test_lifecycle_reversal_reuses_identity_and_restores_full_month(session, employment):
    from app.domain.payroll_automation import refresh

    refresh(session, employment, today=date(2025, 4, 1))
    ids = list(session.scalars(select(PayrollRun.id).order_by(PayrollRun.id)))
    employment.end_date = date(2025, 5, 15)
    payroll.invalidate_forecasts(session, employment.id)
    refresh(session, employment, today=date(2025, 4, 1))
    employment.end_date = None
    employment.is_active = False
    refresh(session, employment, today=date(2025, 4, 1))
    employment.is_active = True
    payroll.invalidate_forecasts(session, employment.id)
    refresh(session, employment, today=date(2025, 4, 1))
    payroll.add_salary_revision(session, employment, date(2025, 5, 1), 4800000)
    result = refresh(session, employment, today=date(2025, 4, 1))
    assert not result["issues"]
    rows = list(session.scalars(select(PayrollRun).order_by(PayrollRun.id)))
    assert [r.id for r in rows] == ids
    assert rows[1].period_end == date(2025, 5, 31)
    assert rows[1].gross_pence == 400000
    assert all(r.status is PayrollRunStatus.forecast and not r.forecast_stale for r in rows)
