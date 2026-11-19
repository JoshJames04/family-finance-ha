"""Reproductions from independent review; all data synthetic."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain import payrun
from app.domain.payroll import PayrollError
from app.persistence.models import (
    PayrollComponentKind as K,
)
from app.persistence.models import (
    PayrollRun,
    PayrollRunStatus,
    PensionMode,
)
from tests.test_payroll_hardening import emp as emp
from tests.test_payroll_hardening import prior


def test_confirmation_does_not_assume_missing_statutory_components_zero(session, emp):
    run = prior(session, emp, status=PayrollRunStatus.forecast)
    run.components.pop()
    with pytest.raises(PayrollError, match="every|explicit"):
        payrun.reconcile(
            session,
            run,
            {K.base_salary: 300000},
            payslip_gross_pence=300000,
            payslip_net_pence=300000,
            taxable_pence=300000,
        )


def test_prior_missing_paye_cannot_be_consumed_as_zero(session, emp):
    r = prior(session, emp)
    r.components.pop()
    r.verified_taxable_pence = 300000
    with pytest.raises(PayrollError, match="PAYE"):
        payrun.generate(
            session, emp, start=date(2025, 5, 1), end=date(2025, 5, 31), pay_date=date(2025, 5, 28)
        )


def test_missing_regular_pay_month_is_not_zero_income(session, emp):
    emp.start_date = date(2024, 1, 1)
    emp.payday_rule = '{"kind":"fixed_day","day":28}'
    r = prior(session, emp)
    r.verified_ytd_taxable_pence = 300000
    r.verified_ytd_tax_pence = 40000
    with pytest.raises(PayrollError, match="missing|gap"):
        payrun.generate(
            session, emp, start=date(2025, 6, 1), end=date(2025, 6, 30), pay_date=date(2025, 6, 28)
        )


def test_ambiguous_legacy_cash_and_tax_year_requires_source_date(session, emp):
    r = prior(session, emp, pay_date=date(2025, 4, 4))
    r.verified_ytd_taxable_pence = 300000
    r.verified_ytd_tax_pence = 40000
    with pytest.raises(PayrollError, match="statutory|PAYE date"):
        payrun.generate(
            session, emp, start=date(2025, 5, 1), end=date(2025, 5, 31), pay_date=date(2025, 5, 28)
        )


def test_rebuild_changes_cutoff_window_with_same_identity(session, emp):
    emp.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    r = payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    rid = r.id
    emp.payday_rule = '{"cutoff_day":20,"pay_rule":{"kind":"fixed_day","day":28}}'
    p = payrun.rebuild_forecasts(session, emp, apply=False)
    payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=p["fingerprint"])
    assert r.id == rid and r.period_start == date(2025, 3, 21) and r.period_end == date(2025, 4, 20)


def test_rebuild_never_reuses_and_deletes_an_existing_actual(session, emp):
    emp.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    actual = prior(session, emp, period_start=date(2025, 3, 21), period_end=date(2025, 4, 20))
    emp.payday_rule = '{"cutoff_day":20,"pay_rule":{"kind":"fixed_day","day":28}}'
    with pytest.raises(PayrollError, match="existing|overlap"):
        payrun.rebuild_forecasts(session, emp, apply=False)
    assert session.get(PayrollRun, actual.id).status is PayrollRunStatus.reconciled


@pytest.mark.parametrize(
    "mode", [PensionMode.net_pay, PensionMode.salary_sacrifice, PensionMode.relief_at_source]
)
def test_gross_only_pension_cash_and_components_agree(session, emp, mode):
    emp.salary_policy = "equal_monthly"
    emp.deduction_mode = "gross_only"
    emp.pension_mode = mode
    emp.pension_fixed_pence = 10000
    r = payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    assert r.net_pence == 290000 and sum(c.forecast_pence for c in r.components) == 290000


def test_disabled_pension_does_not_emit_a_deduction(session, emp):
    emp.pension_mode = PensionMode.none
    emp.pension_fixed_pence = 10000
    r = payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    assert sum(c.forecast_pence for c in r.components) == r.net_pence


def test_taxable_override_invalidates_following_forecast(session, emp):
    r = payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    later = payrun.generate(
        session, emp, start=date(2025, 5, 1), end=date(2025, 5, 31), pay_date=date(2025, 5, 28)
    )
    payrun.apply_override(
        session,
        r,
        kind=K.base_salary,
        delta_pence=100000,
        reason="Explicit synthetic adjustment",
        actor="Test",
    )
    assert later.forecast_stale


def test_prepare_year_includes_salary_after_overtime_cutoff(session, household, emp):
    from app.api import schemas
    from app.api.v1 import employments

    emp.salary_policy = "equal_monthly"
    emp.start_date = date(2025, 4, 20)
    emp.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    employments.prepare_year(schemas.PrepareYearIn(year=2025), household, session, "Test", None)
    row = session.scalar(
        select(PayrollRun).where(
            PayrollRun.employment_id == emp.id, PayrollRun.pay_date == date(2025, 4, 28)
        )
    )
    assert row is not None and row.gross_pence == 110000


@pytest.mark.parametrize("protected", ["actuals", "correction"])
def test_piecework_sync_preserves_protected_forecast(session, emp, protected):
    from app.persistence.models import DeliveryEntry

    emp.employment_type = "piecework"
    emp.deduction_mode = "gross_only"
    entry = DeliveryEntry(
        employment_id=emp.id,
        work_date=date(2025, 4, 10),
        hours=Decimal("4"),
        hourly_rate_pence=1500,
        deliveries=0,
        delivery_rate_pence=0,
        tips_pence=0,
    )
    session.add(entry)
    session.flush()
    run, _ = payrun.sync_piecework_run(session, emp, entry.work_date)
    if protected == "actuals":
        run.components[0].actual_pence = 123
    else:
        run.status = PayrollRunStatus.reconciled
        run = payrun.correct(session, run)
    rid = run.id
    with pytest.raises(PayrollError, match="actuals|correction|bank"):
        payrun.sync_piecework_run(session, emp, entry.work_date)
    assert session.get(PayrollRun, rid) is not None


def test_verified_statutory_date_repairs_legacy_anchor_without_changing_original(session, emp):
    from tests.payroll_helpers import confirmation_payload

    original = prior(session, emp, pay_date=date(2025, 4, 4))
    replacement = payrun.correct(session, original)
    payrun.reconcile(
        session,
        replacement,
        **(
            confirmation_payload(replacement)
            | dict(
                statutory_pay_date=date(2025, 4, 6), ytd_taxable_pence=300000, ytd_tax_pence=40000
            )
        ),
    )
    assert original.statutory_pay_date is None
    assert replacement.statutory_pay_date == date(2025, 4, 6)
    assert payrun.prior_ytd(session, emp, date(2025, 5, 28)) == (300000, 40000)


def test_latest_paired_anchor_covers_missing_earlier_months(session, emp):
    row = prior(
        session,
        emp,
        period_start=date(2025, 5, 1),
        period_end=date(2025, 5, 31),
        pay_date=date(2025, 5, 28),
    )
    row.verified_ytd_taxable_pence = 600000
    row.verified_ytd_tax_pence = 80000
    assert payrun.prior_ytd(session, emp, date(2025, 6, 28)) == (600000, 80000)


def test_standalone_july_without_opening_ytd_is_not_assumed_zero(session, emp):
    with pytest.raises(PayrollError, match="gap|missing"):
        payrun.generate(
            session, emp, start=date(2025, 7, 1), end=date(2025, 7, 31), pay_date=date(2025, 7, 28)
        )


def test_legacy_last_friday_uses_shared_rule_parser(session, emp):
    emp.payday_rule = "last_friday"
    run = payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 25)
    )
    assert run.statutory_pay_date == date(2025, 4, 25)


def test_staleness_follows_statutory_order_not_bank_cash_order(session, emp):
    source = prior(session, emp, pay_date=date(2025, 4, 5), statutory_pay_date=date(2025, 4, 5))
    following = prior(
        session,
        emp,
        period_start=date(2025, 5, 1),
        period_end=date(2025, 5, 31),
        pay_date=date(2025, 4, 4),
        statutory_pay_date=date(2025, 4, 6),
        status=PayrollRunStatus.forecast,
    )
    payrun.mark_later_forecasts_stale(session, source)
    assert following.forecast_stale


@pytest.mark.parametrize("invalid", ["unsupported_year", "invalid_payday"])
def test_payroll_configuration_errors_return_an_actionable_response(tmp_path, invalid):
    from fastapi.testclient import TestClient

    from app.core.settings import Settings
    from app.main import create_app
    from tests.test_payrun import TestApi

    with TestClient(
        create_app(Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"}))
    ) as client:
        h, eid = TestApi()._setup(client)
        client.post(
            f"/api/v1/employments/{eid}/salary-revisions",
            headers=h,
            json={"effective_date": "2023-01-01", "annual_salary": 3600000},
        )
        client.post(
            f"/api/v1/employments/{eid}/tax-code-revisions",
            headers=h,
            json={"effective_date": "2023-01-01", "tax_code": "1257L"},
        )
        year = 2023 if invalid == "unsupported_year" else 2025
        if invalid == "invalid_payday":
            client.patch(
                f"/api/v1/employments/{eid}", headers=h, json={"payday_rule": "not-a-rule"}
            )
        response = client.post(
            f"/api/v1/employments/{eid}/payroll-runs",
            headers=h,
            json={
                "period_start": f"{year}-04-01",
                "period_end": f"{year}-04-30",
                "pay_date": f"{year}-04-28",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]


@pytest.mark.parametrize("change", ["algorithm", "rules"])
def test_upgraded_calculation_marks_forecast_for_review_without_changing_actuals(
    session, emp, monkeypatch, change
):
    from app.domain import schedule, tax
    from app.persistence.models import TaxRuleSet

    run = payrun.generate(
        session, emp, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    old_net = run.net_pence
    if change == "algorithm":
        monkeypatch.setattr(tax, "CALCULATION_VERSION", "synthetic-next-method")
    else:
        current = tax.rule_set(session, "2025-26", emp.jurisdiction)
        session.add(
            TaxRuleSet(
                tax_year="2025-26", jurisdiction=emp.jurisdiction, version=99, params=current.params
            )
        )
        session.flush()
    schedule.rematerialize_employment(session, emp)
    assert run.forecast_stale and run.net_pence == old_net


def _inverted_cash_and_tax_order(session, emp):
    emp.start_date = date(2026, 8, 20)
    emp.payday_rule = '{"kind":"fixed_day","day":6,"adjust":"previous"}'
    early = payrun.generate(
        session, emp, start=date(2026, 9, 1), end=date(2026, 9, 30), pay_date=date(2026, 9, 4)
    )
    irregular = payrun.generate(
        session, emp, start=date(2026, 8, 1), end=date(2026, 8, 31), pay_date=date(2026, 9, 5)
    )
    return early, irregular


def test_backdated_forecast_invalidates_later_statutory_payday(session, emp):
    early, _ = _inverted_cash_and_tax_order(session, emp)
    assert early.forecast_stale


def test_rebuild_orders_dependencies_by_tax_date_even_for_legacy_unpaid_rows(session, emp):
    early, irregular = _inverted_cash_and_tax_order(session, emp)
    early.statutory_pay_date = None
    early.forecast_stale = irregular.forecast_stale = True
    preview = payrun.rebuild_forecasts(session, emp, apply=False)
    assert [c["run_id"] for c in preview["changes"]] == [irregular.id, early.id]


def test_future_fallback_ignores_legacy_slash_rule_years(session, emp):
    from app.domain import payroll
    from app.persistence.models import TaxRuleSet

    session.add(
        TaxRuleSet(tax_year="2027/28", jurisdiction=emp.jurisdiction, version=1, params="{}")
    )
    payroll.add_tax_code_revision(session, emp, date(2027, 1, 1), "1257L M1")
    run = payrun.generate(
        session,
        emp,
        start=date(2027, 4, 1),
        end=date(2027, 4, 30),
        pay_date=date(2027, 4, 28),
        allow_stale_rules=True,
    )
    assert run.rule_set_year == "2026-27" and run.rule_set_version == 3


@pytest.mark.parametrize("signed", [None, False])
def test_unsigned_legacy_unpaid_leave_must_not_silently_become_extra_income(session, emp, signed):
    from app.persistence.models import PayrollComponent

    run = prior(session, emp, status=PayrollRunStatus.forecast)
    run.signed_components = signed
    run.components.append(
        PayrollComponent(kind=K.unpaid_leave, label="Legacy unsigned leave", forecast_pence=10000)
    )
    session.flush()
    with pytest.raises(PayrollError, match="sign|unsigned|legacy"):
        payrun.rebuild_forecasts(session, emp, apply=False)


def _leaver_with_final_overtime(session, emp, work_date, end_date):
    from app.domain import overtime

    emp.salary_policy = "equal_monthly"
    emp.deduction_mode = "gross_only"
    emp.start_date = date(2024, 1, 1)
    emp.end_date = end_date
    emp.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    entry = overtime.create_entry(session, emp, work_date=work_date, hours=Decimal("4"))
    overtime.submit(entry)
    overtime.approve(entry)
    session.flush()
    assert entry.pay_pence == 11076
    return entry


def test_equal_monthly_leaver_receives_final_cutoff_overtime_without_base(session, emp):
    _leaver_with_final_overtime(session, emp, date(2025, 1, 17), date(2025, 1, 20))
    run = payrun.generate(
        session, emp, start=date(2025, 2, 1), end=date(2025, 2, 28), pay_date=date(2025, 2, 28)
    )
    assert run.gross_pence == run.net_pence == 11076
    assert run.period_start == date(2025, 1, 16)
    assert run.period_end == date(2025, 1, 20)
    assert sum(c.forecast_pence for c in run.components if c.kind is K.base_salary) == 0


@pytest.mark.parametrize(
    "work_date,end_date,payday",
    [
        (date(2025, 1, 17), date(2025, 1, 20), date(2025, 2, 28)),
        (date(2024, 12, 17), date(2024, 12, 20), date(2025, 1, 28)),
    ],
)
def test_prepare_year_preserves_final_cutoff_overtime_for_leavers(
    session, household, emp, work_date, end_date, payday
):
    from app.api import schemas
    from app.api.v1 import employments

    _leaver_with_final_overtime(session, emp, work_date, end_date)
    employments.prepare_year(schemas.PrepareYearIn(year=2025), household, session, "Test", None)
    run = session.scalar(
        select(PayrollRun).where(PayrollRun.employment_id == emp.id, PayrollRun.pay_date == payday)
    )
    assert run is not None
    assert run.gross_pence == run.net_pence == 11076


def test_prepare_year_does_not_create_an_empty_month_after_leaving(session, household, emp):
    from app.api import schemas
    from app.api.v1 import employments

    emp.salary_policy = "equal_monthly"
    emp.deduction_mode = "gross_only"
    emp.start_date = date(2024, 1, 1)
    emp.end_date = date(2024, 12, 20)
    emp.payday_rule = '{"cutoff_day":15,"pay_rule":{"kind":"fixed_day","day":28}}'
    employments.prepare_year(schemas.PrepareYearIn(year=2025), household, session, "Test", None)
    assert session.scalar(select(PayrollRun.id).where(PayrollRun.employment_id == emp.id)) is None


def _future_regular_forecast(session, emp):
    from app.domain import schedule

    emp.deduction_mode = "gross_only"
    emp.salary_policy = "equal_monthly"
    emp.payday_rule = '{"kind":"fixed_day","day":28,"adjust":"none"}'
    run = payrun.generate(
        session, emp, start=date(2027, 1, 1), end=date(2027, 1, 31), pay_date=date(2027, 1, 28)
    )
    schedule.rematerialize_payroll_run(session, run, today=date(2026, 1, 1))
    return run


def test_rebuild_moves_regular_cash_date_and_projection_after_rule_change(session, emp):
    from app.persistence.models import ExpectedMovement, ExpectedSource

    run = _future_regular_forecast(session, emp)
    emp.payday_rule = '{"kind":"fixed_day","day":25,"adjust":"none"}'
    preview = payrun.rebuild_forecasts(session, emp, apply=False)
    change = next(c for c in preview["changes"] if c["run_id"] == run.id)
    assert change["old_pay_date"] == "2027-01-28"
    assert change["new_pay_date"] == "2027-01-25"
    assert run.pay_date == date(2027, 1, 28)
    payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
    assert run.pay_date == run.statutory_pay_date == date(2027, 1, 25)
    rows = list(
        session.scalars(
            select(ExpectedMovement).where(
                ExpectedMovement.source_type == ExpectedSource.payroll_run,
                ExpectedMovement.source_id == run.id,
            )
        )
    )
    assert len(rows) == 1 and rows[0].expected_date == date(2027, 1, 25)


def test_payday_edit_preserves_legacy_calendar_provenance_during_auto_refresh(
    session, household, emp
):
    import json

    from app.api import schemas
    from app.api.v1 import employments

    run = _future_regular_forecast(session, emp)
    snapshot = json.loads(run.calculation_inputs)
    snapshot.pop("payday_rule", None)
    snapshot.pop("payroll_month", None)
    run.calculation_inputs = json.dumps(snapshot)
    employments.patch_employment(
        emp.id,
        schemas.EmploymentPatch(payday_rule='{"kind":"fixed_day","day":25,"adjust":"none"}'),
        household,
        session,
        "Test",
        None,
    )
    assert run.pay_date == date(2027, 1, 25)
    assert not run.forecast_stale


def test_irregular_forecast_date_is_preserved_when_regular_rule_changes(session, emp):
    emp.deduction_mode = "gross_only"
    emp.payday_rule = '{"kind":"fixed_day","day":28,"adjust":"none"}'
    run = payrun.generate(
        session, emp, start=date(2027, 1, 1), end=date(2027, 1, 31), pay_date=date(2027, 1, 20)
    )
    emp.payday_rule = '{"kind":"fixed_day","day":25,"adjust":"none"}'
    preview = payrun.rebuild_forecasts(session, emp, apply=False)
    payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
    assert run.pay_date == date(2027, 1, 20)


def test_rebuild_keeps_leavers_payroll_month_after_cutoff_clips_work_period(session, emp):
    _leaver_with_final_overtime(session, emp, date(2025, 1, 17), date(2025, 1, 20))
    run = payrun.generate(
        session, emp, start=date(2025, 2, 1), end=date(2025, 2, 28), pay_date=date(2025, 2, 28)
    )
    preview = payrun.rebuild_forecasts(session, emp, apply=False)
    payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
    assert run.gross_pence == run.net_pence == 11076
    assert run.pay_date == date(2025, 2, 28)


def test_empty_piecework_keeps_identity_without_projecting_zero_income(session, emp):
    from app.domain import schedule
    from app.persistence.models import DeliveryEntry, ExpectedMovement, ExpectedSource

    emp.employment_type = "piecework"
    emp.deduction_mode = "gross_only"
    emp.payday_rule = '{"kind":"fixed_day","day":28,"adjust":"none"}'
    entry = DeliveryEntry(
        employment_id=emp.id,
        work_date=date(2027, 1, 8),
        hours=Decimal("4"),
        hourly_rate_pence=1500,
        deliveries=0,
        delivery_rate_pence=0,
        tips_pence=0,
    )
    session.add(entry)
    session.flush()
    run, _ = payrun.sync_piecework_run(session, emp, entry.work_date)
    rid = run.id
    schedule.rematerialize_payroll_run(session, run)
    session.delete(entry)
    session.flush()
    empty, _ = payrun.sync_piecework_run(session, emp, date(2027, 1, 8))
    assert empty.id == rid and empty.gross_pence == empty.net_pence == 0
    schedule.rematerialize_payroll_run(session, empty)
    assert not list(
        session.scalars(
            select(ExpectedMovement).where(
                ExpectedMovement.source_type == ExpectedSource.payroll_run,
                ExpectedMovement.source_id == rid,
            )
        )
    )


def test_irregular_date_stays_explicit_after_matching_an_intermediate_regular_rule(session, emp):
    emp.deduction_mode = "gross_only"
    emp.payday_rule = '{"kind":"fixed_day","day":28,"adjust":"none"}'
    run = payrun.generate(
        session, emp, start=date(2027, 1, 1), end=date(2027, 1, 31), pay_date=date(2027, 1, 20)
    )
    for day in (20, 25):
        emp.payday_rule = f'{{"kind":"fixed_day","day":{day},"adjust":"none"}}'
        preview = payrun.rebuild_forecasts(session, emp, apply=False)
        payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
        assert run.pay_date == date(2027, 1, 20)


@pytest.mark.parametrize("change,new_deduction", [("salary", 36923), ("working_week", 34615)])
@pytest.mark.parametrize("fixed", [0, 1234])
def test_rebuild_recalculates_original_leave_days_without_double_counting_cash(
    session, emp, change, new_deduction, fixed
):
    import json

    from app.domain import payroll

    emp.deduction_mode = "gross_only"
    emp.salary_policy = "equal_monthly"
    run = payrun.generate(
        session,
        emp,
        start=date(2027, 1, 1),
        end=date(2027, 1, 31),
        pay_date=date(2027, 1, 28),
        unpaid_leave_days=Decimal("2"),
        unpaid_leave_pence=fixed,
    )
    if change == "salary":
        payroll.add_salary_revision(session, emp, date(2027, 1, 1), 4800000)
        base = 400000
    else:
        emp.work_days_per_week = Decimal("4")
        base = 300000
    for _ in range(2):
        preview = payrun.rebuild_forecasts(session, emp, apply=False)
        payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
        assert run.net_pence == base - new_deduction - fixed
        inputs = json.loads(run.calculation_inputs)
        assert Decimal(inputs["unpaid_leave_days"]) == 2
        assert inputs["unpaid_leave_fixed_pence"] == fixed


def test_rebuild_preserves_fixed_cash_leave_when_salary_changes(session, emp):
    from app.domain import payroll

    emp.deduction_mode = "gross_only"
    emp.salary_policy = "equal_monthly"
    run = payrun.generate(
        session,
        emp,
        start=date(2027, 1, 1),
        end=date(2027, 1, 31),
        pay_date=date(2027, 1, 28),
        unpaid_leave_pence=1234,
    )
    payroll.add_salary_revision(session, emp, date(2027, 1, 1), 4800000)
    preview = payrun.rebuild_forecasts(session, emp, apply=False)
    payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
    assert run.net_pence == 398766


def test_legacy_leave_does_not_invent_a_missing_day_count(session, emp):
    import json

    from app.domain import payroll

    emp.deduction_mode = "gross_only"
    emp.salary_policy = "equal_monthly"
    run = payrun.generate(
        session,
        emp,
        start=date(2027, 1, 1),
        end=date(2027, 1, 31),
        pay_date=date(2027, 1, 28),
        unpaid_leave_days=Decimal("2"),
    )
    inputs = json.loads(run.calculation_inputs)
    inputs.pop("unpaid_leave_days", None)
    inputs.pop("unpaid_leave_fixed_pence", None)
    run.calculation_inputs = json.dumps(inputs)
    payroll.add_salary_revision(session, emp, date(2027, 1, 1), 4800000)
    for _ in range(2):
        preview = payrun.rebuild_forecasts(session, emp, apply=False)
        assert any("leave" in w.lower() and "unknown" in w.lower() for w in preview["warnings"])
        payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
        assert run.net_pence == 372308


@pytest.mark.parametrize("day", [25, 31])
def test_payday_edit_auto_refresh_does_not_add_a_second_salary(session, household, emp, day):
    from app.api import schemas
    from app.api.v1 import employments
    from app.domain import schedule
    from app.persistence.models import Account, ExpectedMovement, ExpectedSource

    account = Account(
        household_id=household.id,
        name="Synthetic pay account",
        type="personal",
        opening_date=date(2026, 1, 1),
    )
    session.add(account)
    session.flush()
    emp.paid_into_account_id = account.id
    run = _future_regular_forecast(session, emp)
    employments.patch_employment(
        emp.id,
        schemas.EmploymentPatch(payday_rule=f'{{"kind":"fixed_day","day":{day},"adjust":"none"}}'),
        household,
        session,
        "Test",
        None,
    )
    for _ in range(2):
        schedule.rematerialize_employment(session, emp, today=date(2027, 1, 1), horizon_days=31)
        rows = list(
            session.scalars(
                select(ExpectedMovement).where(
                    ExpectedMovement.account_id == account.id,
                    ExpectedMovement.expected_date >= date(2027, 1, 1),
                    ExpectedMovement.expected_date <= date(2027, 1, 31),
                )
            )
        )
        assert len(rows) == 1 and rows[0].source_type is ExpectedSource.payroll_run
        assert rows[0].expected_date == date(2027, 1, day)
    preview = payrun.rebuild_forecasts(session, emp, apply=False)
    payrun.rebuild_forecasts(session, emp, apply=True, fingerprint=preview["fingerprint"])
    schedule.rematerialize_employment(session, emp, today=date(2027, 1, 1), horizon_days=31)
    rows = list(
        session.scalars(
            select(ExpectedMovement).where(
                ExpectedMovement.account_id == account.id,
                ExpectedMovement.expected_date >= date(2027, 1, 1),
                ExpectedMovement.expected_date <= date(2027, 1, 31),
            )
        )
    )
    assert len(rows) == 1 and rows[0].expected_date == date(2027, 1, day)
    assert run.id == rows[0].source_id


def test_covered_payroll_month_accounts_for_cash_shift_into_previous_month(session, household, emp):
    from app.domain import schedule
    from app.persistence.models import Account, ExpectedMovement, ExpectedSource

    account = Account(
        household_id=household.id,
        name="Synthetic shifted pay",
        type="personal",
        opening_date=date(2026, 1, 1),
    )
    session.add(account)
    session.flush()
    emp.paid_into_account_id = account.id
    emp.deduction_mode = "gross_only"
    emp.salary_policy = "equal_monthly"
    emp.payday_rule = '{"kind":"fixed_day","day":28,"adjust":"none"}'
    run = payrun.generate(
        session, emp, start=date(2027, 5, 1), end=date(2027, 5, 31), pay_date=date(2027, 5, 28)
    )
    schedule.rematerialize_payroll_run(session, run, today=date(2027, 4, 1))
    emp.payday_rule = '{"kind":"fixed_day","day":1,"adjust":"previous"}'
    schedule.rematerialize_employment(session, emp, today=date(2027, 4, 1), horizon_days=60)
    assert not session.scalar(
        select(ExpectedMovement.id).where(
            ExpectedMovement.source_type == ExpectedSource.employment,
            ExpectedMovement.source_id == emp.id,
            ExpectedMovement.expected_date == date(2027, 4, 30),
        )
    )


def test_paid_salary_month_does_not_get_a_new_generic_estimate_after_rule_change(
    session, household, emp
):
    from app.domain import schedule
    from app.persistence.models import Account, ExpectedMovement, ExpectedSource
    from tests.payroll_helpers import confirm

    account = Account(
        household_id=household.id,
        name="Synthetic received pay",
        type="personal",
        opening_date=date(2026, 1, 1),
    )
    session.add(account)
    session.flush()
    emp.paid_into_account_id = account.id
    emp.deduction_mode = "gross_only"
    emp.salary_policy = "equal_monthly"
    emp.payday_rule = '{"kind":"fixed_day","day":10,"adjust":"none"}'
    run = payrun.generate(
        session, emp, start=date(2026, 11, 1), end=date(2026, 11, 30), pay_date=date(2026, 11, 10)
    )
    confirm(session, run)
    emp.payday_rule = '{"kind":"fixed_day","day":28,"adjust":"none"}'
    schedule.rematerialize_employment(session, emp, today=date(2026, 11, 18), horizon_days=30)
    assert not session.scalar(
        select(ExpectedMovement.id).where(
            ExpectedMovement.source_type == ExpectedSource.employment,
            ExpectedMovement.source_id == emp.id,
            ExpectedMovement.expected_date == date(2026, 11, 28),
        )
    )
    assert run.pay_date == date(2026, 11, 10) and run.status is PayrollRunStatus.reconciled


@pytest.mark.parametrize("was_weekly", [False, True])
def test_frequency_change_defers_generic_estimates_while_old_forecasts_exist(
    session, household, emp, was_weekly
):
    from app.domain import schedule
    from app.persistence.models import Account, ExpectedMovement, ExpectedSource

    account = Account(
        household_id=household.id,
        name="Synthetic frequency transition",
        type="personal",
        opening_date=date(2026, 1, 1),
    )
    session.add(account)
    session.flush()
    emp.paid_into_account_id = account.id
    emp.deduction_mode = "gross_only"
    emp.payday_rule = (
        '{"kind":"weekly","weekday":4,"adjust":"none"}'
        if was_weekly
        else '{"kind":"fixed_day","day":28,"adjust":"none"}'
    )
    payday = date(2027, 1, 29) if was_weekly else date(2027, 1, 28)
    run = payrun.generate(
        session,
        emp,
        start=date(2027, 1, 23) if was_weekly else date(2027, 1, 1),
        end=payday if was_weekly else date(2027, 1, 31),
        pay_date=payday,
    )
    schedule.rematerialize_payroll_run(session, run, today=date(2027, 1, 1))
    emp.payday_rule = (
        '{"kind":"fixed_day","day":25,"adjust":"none"}'
        if was_weekly
        else '{"kind":"weekly","weekday":1,"adjust":"none"}'
    )
    schedule.rematerialize_employment(session, emp, today=date(2027, 1, 1), horizon_days=60)
    assert not list(
        session.scalars(
            select(ExpectedMovement.id).where(
                ExpectedMovement.source_type == ExpectedSource.employment,
                ExpectedMovement.source_id == emp.id,
            )
        )
    )
    assert run.pay_date == payday


def test_weekly_payday_edit_keeps_the_prepared_week_covered(session, household, emp):
    from app.domain import schedule
    from app.persistence.models import Account, ExpectedMovement, ExpectedSource

    account = Account(
        household_id=household.id,
        name="Synthetic weekly pay",
        type="personal",
        opening_date=date(2026, 1, 1),
    )
    session.add(account)
    session.flush()
    emp.paid_into_account_id = account.id
    emp.deduction_mode = "gross_only"
    emp.payday_rule = '{"kind":"weekly","weekday":4,"adjust":"none"}'
    run = payrun.generate(
        session, emp, start=date(2027, 1, 23), end=date(2027, 1, 29), pay_date=date(2027, 1, 29)
    )
    schedule.rematerialize_payroll_run(session, run, today=date(2027, 1, 25))
    emp.payday_rule = '{"kind":"weekly","weekday":1,"adjust":"none"}'
    schedule.rematerialize_employment(session, emp, today=date(2027, 1, 25), horizon_days=7)
    assert not session.scalar(
        select(ExpectedMovement.id).where(
            ExpectedMovement.source_type == ExpectedSource.employment,
            ExpectedMovement.source_id == emp.id,
            ExpectedMovement.expected_date == date(2027, 1, 26),
        )
    )
