import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.domain import goals, loans, payroll, payrun, tax
from app.persistence.models import (
    Account,
    Employment,
    GoalType,
    Loan,
    LoanDirection,
    Repayment,
    SavingsGoal,
    Transaction,
    TransactionStatus,
    Transfer,
    UserProfile,
)

TODAY = date(2025, 4, 10)


def account(session, household):
    a = Account(
        household_id=household.id,
        name="Synthetic pot",
        type="personal",
        opening_date=date(2025, 1, 1),
    )
    session.add(a)
    session.flush()
    return a


def loan(session, household):
    row = Loan(
        household_id=household.id,
        party="Synthetic loan",
        direction=LoanDirection.we_owe,
        principal_pence=100000,
        start_date=date(2025, 1, 1),
        expected_payment_pence=10000,
    )
    session.add(row)
    session.flush()
    return row


def test_same_day_repayment_running_balance(session, household):
    row = loan(session, household)
    session.add_all(
        [
            Repayment(loan_id=row.id, date=TODAY, amount_pence=10000),
            Repayment(loan_id=row.id, date=TODAY, amount_pence=20000),
        ]
    )
    session.flush()
    assert [balance for _, balance in loans.repayment_balances(session, row)] == [90000, 70000]


def test_duplicate_bank_transaction_does_not_pay_loan_twice(session, household):
    from app.api import schemas
    from app.api.v1 import loans as api

    row = loan(session, household)
    a = account(session, household)
    tx = Transaction(account_id=a.id, date=TODAY, amount_pence=-10000, payee="Synthetic repayment")
    session.add(tx)
    session.flush()
    body = schemas.RepaymentIn(date=TODAY, amount=10000, transaction_id=tx.id)
    api.add_repayment(row.id, body, household, session, "audit", None)
    with pytest.raises(HTTPException) as error:
        api.add_repayment(row.id, body, household, session, "audit", None)
    assert error.value.status_code == 409
    assert loans.outstanding(session, row, on=TODAY) == 90000


def test_goal_excludes_voided_inbound_transfer(session, household):
    pot = account(session, household)
    goal = SavingsGoal(
        household_id=household.id,
        name="Synthetic goal",
        goal_type=GoalType.holiday,
        target_pence=120000,
        start_date=date(2025, 1, 1),
        target_date=date(2025, 12, 1),
        account_id=pot.id,
    )
    transfer = Transfer(household_id=household.id)
    session.add_all([goal, transfer])
    session.flush()
    credit = Transaction(
        account_id=pot.id,
        date=TODAY,
        amount_pence=10000,
        payee="Synthetic transfer",
        transfer_id=transfer.id,
        status=TransactionStatus.void,
    )
    session.add(credit)
    session.flush()
    assert goals.contributed_pence(session, goal) == 0


def test_goal_excludes_future_and_pre_goal_contributions(session, household):
    pot = account(session, household)
    goal = SavingsGoal(
        household_id=household.id,
        name="Synthetic goal",
        goal_type=GoalType.holiday,
        target_pence=120000,
        start_date=date(2025, 4, 1),
        target_date=date(2025, 12, 1),
        account_id=pot.id,
    )
    session.add(goal)
    for on in (date(2025, 3, 1), date(2025, 5, 1)):
        transfer = Transfer(household_id=household.id)
        session.add(transfer)
        session.flush()
        session.add(
            Transaction(
                account_id=pot.id,
                date=on,
                amount_pence=10000,
                payee="Synthetic transfer",
                transfer_id=transfer.id,
                status=TransactionStatus.cleared,
            )
        )
    session.flush()
    assert goals.progress(session, goal, today=TODAY).contributed_pence == 0


def test_supported_flat_code_uses_versioned_rate_parameters():
    params = next(
        row["params"]
        for row in tax.embedded_rule_sets()
        if row["tax_year"] == "2025-26" and row["jurisdiction"] == "england_wales"
    )
    altered = json.loads(json.dumps(params))
    altered["bands"][0]["rate"] = 3000
    before = tax.compute_period(gross_pence=300000, tax_code="BR", params=params, period=1)
    after = tax.compute_period(gross_pence=300000, tax_code="BR", params=altered, period=1)
    assert after.tax_pence != before.tax_pence


def test_salary_revisions_are_private_between_members(session, household, tmp_path):
    from fastapi.testclient import TestClient

    from app.core.settings import Settings
    from app.main import create_app

    owner = UserProfile(household_id=household.id, display_name="Synthetic owner", role="member")
    viewer = UserProfile(household_id=household.id, display_name="Synthetic viewer", role="member")
    session.add_all([owner, viewer])
    session.flush()
    e = Employment(
        household_id=household.id,
        profile_id=owner.id,
        employer="Synthetic employer",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
    )
    session.add(e)
    session.flush()
    payroll.add_salary_revision(session, e, date(2025, 1, 1), 3600000)
    session.commit()
    settings = Settings.load(
        {
            "FF_DATA_DIR": str(tmp_path / "app"),
            "FF_DATABASE_URL": str(session.get_bind().url),
            "FF_SCHEDULER_TICK_SECONDS": "0",
        }
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/api/v1/employments/{e.id}/salary-revisions",
            headers={"X-Household-Id": str(household.id), "X-Profile-Id": str(viewer.id)},
        )
    assert response.status_code == 404


def test_goal_private_account_contribution_ledger_not_disclosed(session, household, tmp_path):
    from fastapi.testclient import TestClient

    from app.core.settings import Settings
    from app.main import create_app

    owner = UserProfile(household_id=household.id, display_name="Synthetic owner", role="member")
    viewer = UserProfile(household_id=household.id, display_name="Synthetic viewer", role="member")
    session.add_all([owner, viewer])
    session.flush()
    pot = account(session, household)
    pot.owner_profile_id = owner.id
    goal = SavingsGoal(
        household_id=household.id,
        name="Synthetic private goal",
        goal_type=GoalType.holiday,
        target_pence=120000,
        start_date=TODAY,
        account_id=pot.id,
    )
    transfer = Transfer(household_id=household.id)
    session.add_all([goal, transfer])
    session.flush()
    session.add(
        Transaction(
            account_id=pot.id,
            date=TODAY,
            amount_pence=10000,
            payee="Synthetic private transfer",
            transfer_id=transfer.id,
        )
    )
    session.commit()
    settings = Settings.load(
        {
            "FF_DATA_DIR": str(tmp_path / "app"),
            "FF_DATABASE_URL": str(session.get_bind().url),
            "FF_SCHEDULER_TICK_SECONDS": "0",
        }
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/api/v1/savings-goals/{goal.id}/contributions",
            headers={"X-Household-Id": str(household.id), "X-Profile-Id": str(viewer.id)},
        )
    assert response.status_code == 404


def test_k_code_tax_respects_half_pay_limit():
    params = next(
        row["params"]
        for row in tax.embedded_rule_sets()
        if row["tax_year"] == "2025-26" and row["jurisdiction"] == "england_wales"
    )
    result = tax.compute_period(gross_pence=10000, tax_code="K5000", params=params, period=1)
    assert result.tax_pence <= 5000


def test_category_m_employee_ni_matches_category_a():
    params = next(
        row["params"]
        for row in tax.embedded_rule_sets()
        if row["tax_year"] == "2025-26" and row["jurisdiction"] == "england_wales"
    )
    regular = tax.compute_period(
        gross_pence=600000, tax_code="1257L", params=params, period=1, ni_category="A"
    )
    under_21 = tax.compute_period(
        gross_pence=600000, tax_code="1257L", params=params, period=1, ni_category="M"
    )
    assert under_21.ni_pence == regular.ni_pence


def test_repeated_run_generation_does_not_duplicate_expected_income(session, household):
    from app.domain import schedule
    from app.persistence.models import ExpectedMovement, ExpectedSource

    pot = account(session, household)
    p = UserProfile(household_id=household.id, display_name="Synthetic employee", role="member")
    session.add(p)
    session.flush()
    e = Employment(
        household_id=household.id,
        profile_id=p.id,
        employer="Synthetic employer",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
        paid_into_account_id=pot.id,
    )
    session.add(e)
    session.flush()
    payroll.add_salary_revision(session, e, date(2025, 1, 1), 3600000)
    payroll.add_tax_code_revision(session, e, date(2025, 1, 1), "1257L")
    for _ in range(2):
        run = payrun.generate(
            session, e, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
        )
        schedule.rematerialize_payroll_run(session, run, today=TODAY)
    movements = list(
        session.scalars(
            select(ExpectedMovement).where(
                ExpectedMovement.source_type == ExpectedSource.payroll_run
            )
        )
    )
    assert len(movements) == 1


def test_unsupported_future_year_is_explicit_and_stale_forecast_is_pinned(session, household):
    import pytest

    from app.domain import schedule

    pot = account(session, household)
    p = UserProfile(
        household_id=household.id, display_name="Synthetic future employee", role="member"
    )
    session.add(p)
    session.flush()
    e = Employment(
        household_id=household.id,
        profile_id=p.id,
        employer="Synthetic future employer",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
        paid_into_account_id=pot.id,
        payday_rule=json.dumps({"kind": "fixed_day", "day": 28, "adjust": "none"}),
    )
    session.add(e)
    session.flush()
    payroll.add_salary_revision(session, e, date(2025, 1, 1), 3600000)
    payroll.add_tax_code_revision(session, e, date(2025, 1, 1), "1257L")
    with pytest.raises(tax.TaxError):
        payrun.generate(
            session, e, start=date(2027, 6, 1), end=date(2027, 6, 30), pay_date=date(2027, 6, 28)
        )
    assert schedule._employment_net_estimate(session, e, date(2027, 6, 28)) is None
    run = payrun.generate(
        session,
        e,
        start=date(2027, 6, 1),
        end=date(2027, 6, 30),
        pay_date=date(2027, 6, 28),
        allow_stale_rules=True,
    )
    assert run.tax_year == "2027-28"
    assert run.rule_set_year == "2026-27"


def test_configured_cutoff_persists_into_generated_run(session, household):
    p = UserProfile(
        household_id=household.id, display_name="Synthetic cutoff employee", role="member"
    )
    session.add(p)
    session.flush()
    e = Employment(
        household_id=household.id,
        profile_id=p.id,
        employer="Synthetic cutoff employer",
        jurisdiction="england_wales",
        contracted_hours=Decimal("37.5"),
        payday_rule=json.dumps(
            {"kind": "fixed_day", "day": 28, "adjust": "none", "cutoff_day": 15}
        ),
    )
    session.add(e)
    session.flush()
    payroll.add_salary_revision(session, e, date(2025, 1, 1), 3600000)
    payroll.add_tax_code_revision(session, e, date(2025, 1, 1), "1257L")
    run = payrun.generate(
        session, e, start=date(2025, 4, 1), end=date(2025, 4, 30), pay_date=date(2025, 4, 28)
    )
    assert (run.period_start, run.period_end) == (date(2025, 3, 16), date(2025, 4, 15))


@pytest.mark.parametrize("year,month,day,adjust", [(2027, 1, 1, "previous"),
                                                  (2026, 5, 31, "next")])
def test_cutoff_uses_payroll_month_when_adjusted_payday_crosses_month(
        session, household, year, month, day, adjust):
    import calendar

    from app.domain import pay_calendar

    profile = UserProfile(household_id=household.id, display_name="Synthetic", role="member")
    session.add(profile)
    session.flush()
    employment = Employment(
        household_id=household.id, profile_id=profile.id, employer="Synthetic",
        jurisdiction="england_wales", contracted_hours=Decimal("37.5"),
        deduction_mode="gross_only", payday_rule=json.dumps({
            "kind": "fixed_day", "day": day, "adjust": adjust, "cutoff_day": 15}),
    )
    session.add(employment)
    session.flush()
    payroll.add_salary_revision(session, employment, date(2025, 1, 1), 3600000)
    window = pay_calendar.period(pay_calendar.parse_calendar(employment.payday_rule), year, month)
    assert window.pay_date.month != month
    run = payrun.generate(session, employment, start=date(year, month, 1),
                          end=date(year, month, calendar.monthrange(year, month)[1]),
                          pay_date=window.pay_date)
    assert (run.period_start, run.period_end) == (window.start, window.cutoff)
    assert payrun.generate(session, employment, start=window.start, end=window.cutoff,
                           pay_date=window.pay_date).id == run.id


def test_seasonal_remaining_uses_actual_and_exposes_commitments(session, household):
    from app.domain import seasonal
    from app.persistence.models import Gift, SeasonalEvent

    e = SeasonalEvent(
        household_id=household.id,
        year=2025,
        type="synthetic",
        name="Synthetic",
        budget_pence=100000,
    )
    session.add(e)
    session.flush()
    session.add(
        Gift(
            event_id=e.id,
            recipient="Synthetic",
            idea="Synthetic",
            planned_pence=20000,
            actual_pence=10000,
        )
    )
    session.add(Gift(event_id=e.id, recipient="Other", idea="Synthetic", planned_pence=30000))
    session.flush()
    t = seasonal.totals(session, e)
    assert t.remaining_pence == 90000
    assert t.remaining_after_commitments_pence == 60000


def test_seasonal_private_event_not_visible_to_other_member(session, household, tmp_path):
    from fastapi.testclient import TestClient

    from app.core.settings import Settings
    from app.main import create_app

    owner = UserProfile(household_id=household.id, display_name="Synthetic owner", role="member")
    viewer = UserProfile(household_id=household.id, display_name="Synthetic viewer", role="member")
    session.add_all([owner, viewer])
    session.commit()
    settings = Settings.load(
        {
            "FF_DATA_DIR": str(tmp_path / "app"),
            "FF_DATABASE_URL": str(session.get_bind().url),
            "FF_SCHEDULER_TICK_SECONDS": "0",
        }
    )
    with TestClient(create_app(settings)) as client:
        h = {"X-Household-Id": str(household.id), "X-Profile-Id": str(owner.id)}
        created = client.post(
            "/api/v1/seasonal-events",
            headers=h,
            json={
                "year": 2025,
                "type": "synthetic",
                "name": "Synthetic private event",
                "budget": 100000,
                "owner_profile_id": owner.id,
            },
        )
        assert created.status_code == 201
        h["X-Profile-Id"] = str(viewer.id)
        assert client.get("/api/v1/seasonal-events", headers=h).json() == []
        assert (
            client.get(f"/api/v1/seasonal-events/{created.json()['id']}", headers=h).status_code
            == 404
        )


def test_payee_list_and_rename_respect_private_accounts(session, household, tmp_path):
    from fastapi.testclient import TestClient

    from app.core.settings import Settings
    from app.main import create_app

    owner = UserProfile(household_id=household.id, display_name="Synthetic owner", role="member")
    viewer = UserProfile(household_id=household.id, display_name="Synthetic viewer", role="member")
    session.add_all([owner, viewer])
    session.flush()
    pot = account(session, household)
    pot.owner_profile_id = owner.id
    tx = Transaction(account_id=pot.id, date=TODAY, amount_pence=-10000, payee="Private payee")
    session.add(tx)
    session.commit()
    settings = Settings.load(
        {
            "FF_DATA_DIR": str(tmp_path / "app"),
            "FF_DATABASE_URL": str(session.get_bind().url),
            "FF_SCHEDULER_TICK_SECONDS": "0",
        }
    )
    with TestClient(create_app(settings)) as client:
        h = {"X-Household-Id": str(household.id), "X-Profile-Id": str(viewer.id)}
        assert client.get("/api/v1/payees", headers=h).json() == []
        response = client.post(
            "/api/v1/payees/rename",
            headers=h,
            json={"source": "Private payee", "target": "Changed"},
        )
        assert response.status_code == 200
        assert response.json()["renamed"] == 0
    session.refresh(tx)
    assert tx.payee == "Private payee"


def test_current_ni_category_matrix_uses_employee_rates():
    params = next(
        row["params"]
        for row in tax.embedded_rule_sets()
        if row["tax_year"] == "2025-26" and row["jurisdiction"] == "england_wales"
    )
    for category in ("A", "H", "M"):
        assert (
            tax.compute_period(
                gross_pence=600000, tax_code="NT", params=params, ni_category=category
            ).ni_pence
            == 28754
        )
    for category in ("J", "Z"):
        assert (
            tax.compute_period(
                gross_pence=600000, tax_code="NT", params=params, ni_category=category
            ).ni_pence
            == 9904
        )


def test_database_rejects_duplicate_bank_attribution(session, household):
    from sqlalchemy.exc import IntegrityError

    row = loan(session, household)
    a = account(session, household)
    tx = Transaction(account_id=a.id, date=TODAY, amount_pence=-10000, payee="Synthetic")
    session.add(tx)
    session.flush()
    session.add(Repayment(loan_id=row.id, date=TODAY, amount_pence=10000, transaction_id=tx.id))
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Repayment(loan_id=row.id, date=TODAY, amount_pence=10000, transaction_id=tx.id))
        session.flush()


def test_shared_and_private_events_can_share_type_and_year(session, household):
    from app.persistence.models import SeasonalEvent

    owner = UserProfile(household_id=household.id, display_name="Synthetic", role="member")
    session.add(owner)
    session.flush()
    session.add(SeasonalEvent(household_id=household.id, year=2025, type="synthetic",
                             name="Shared", budget_pence=10000))
    session.add(SeasonalEvent(household_id=household.id, year=2025, type="synthetic",
                             name="Private", budget_pence=10000, owner_profile_id=owner.id))
    session.flush()
    assert len(list(session.scalars(select(SeasonalEvent)))) == 2


@pytest.mark.parametrize("code,period,frequency,prior", [
    ("K5000", 1, 12, 0), ("K5000M1", 8, 12, 0),
    ("K5000", 8, 12, 2000), ("K5000W1", 10, 52, 0),
])
def test_k_limit_applies_with_ytd_and_non_cumulative_codes(code, period, frequency, prior):
    params = next(row["params"] for row in tax.embedded_rule_sets()
                  if row["tax_year"] == "2025-26" and row["jurisdiction"] == "england_wales")
    result = tax.compute_period(gross_pence=10000, tax_code=code, params=params,
                                period=period, periods_per_year=frequency,
                                ytd_taxable_gross_pence=10000, ytd_tax_pence=prior)
    assert result.tax_pence == 5000
