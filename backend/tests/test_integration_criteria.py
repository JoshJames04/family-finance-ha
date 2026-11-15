"""Named numeric criteria use canonical values and preserve source privacy."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.api.deps import resolve_profile
from app.domain import accounts
from app.main import create_app
from app.persistence.models import (
    AccountType,
    Budget,
    BudgetLine,
    Category,
    Employment,
    ExpectedMovement,
    ExpectedSource,
    Gift,
    SavingsGoal,
    SeasonalEvent,
    Transaction,
    Transfer,
    UserProfile,
)
from app.persistence.session import get_session


def client(session, profile=None):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[resolve_profile] = lambda: profile
    return TestClient(app)


def account(session, household, **kwargs):
    return accounts.open_account(session, household.id, "Synthetic account", AccountType.joint,
                                 10_000, date.today(), **kwargs)


def test_named_cashflow_budget_and_seasonal_values_are_penny_accurate(session, household):
    shared = account(session, household)
    category = Category(household_id=household.id, name="Synthetic category")
    session.add(category)
    session.flush()
    budget = Budget(household_id=household.id, name="Synthetic budget", status="approved",
                    period_type="custom", start_date=date.today(),
                    end_date=date.today() + timedelta(days=30))
    event = SeasonalEvent(household_id=household.id, name="Synthetic season", type="synthetic",
                          year=date.today().year, budget_pence=10_000)
    session.add_all([budget, event])
    session.flush()
    session.add_all([
        BudgetLine(budget_id=budget.id, category_id=category.id, side="expense",
                   original_pence=10_000),
        Transaction(account_id=shared.id, date=date.today(), amount_pence=-2_500,
                    payee="Synthetic expense", category_id=category.id, status="cleared"),
        ExpectedMovement(household_id=household.id, account_id=shared.id,
                         source_type="manual", source_id=1,
                         expected_date=date.today() + timedelta(days=1),
                         amount_pence=-3_000, label="Synthetic plan", confidence="scheduled"),
        Gift(event_id=event.id, recipient="Synthetic recipient", idea="Synthetic gift",
             planned_pence=2_500, actual_pence=2_500),
    ])
    session.flush()
    response = client(session).get("/api/v1/integration/items")
    assert response.status_code == 200
    rows = {item["family"]: item for item in response.json()["items"]}
    assert rows["safe_to_spend"]["value"] == "45.00"
    assert rows["projected_balances"]["value"] == "45.00"
    assert rows["budget_remaining"]["value"] == "75.00"
    assert rows["budget_usage"]["value"] == "25.00"
    assert rows["seasonal_remaining"]["value"] == "75.00"
    assert rows["seasonal_usage"]["value"] == "25.00"
    assert rows["budget_usage"]["unit"] == "%"
    assert rows["safe_to_spend"]["unit"] == "GBP"
    identity = rows["safe_to_spend"]["id"]
    shared.name = "Renamed account"
    session.flush()
    renamed = client(session).get("/api/v1/integration/items").json()["items"]
    item = next(row for row in renamed if row["family"] == "safe_to_spend")
    assert item["id"] == identity
    assert "Renamed account" in item["name"]


def test_shared_forecast_omits_private_payroll_inputs_and_payday_identity(session, household):
    shared = account(session, household)
    owner = UserProfile(household_id=household.id, display_name="Synthetic owner")
    session.add(owner)
    session.flush()
    employment = Employment(household_id=household.id, profile_id=owner.id,
                            employer="Private employer", jurisdiction="england_wales",
                            contracted_hours=37, paid_into_account_id=shared.id)
    session.add(employment)
    session.flush()
    session.add(ExpectedMovement(
        household_id=household.id, account_id=shared.id, source_type=ExpectedSource.employment,
        source_id=employment.id, expected_date=date.today() + timedelta(days=7),
        amount_pence=100_000, label="Private pay", confidence="estimated"))
    session.flush()
    shared_rows = client(session).get("/api/v1/integration/items").json()["items"]
    assert not {row["family"] for row in shared_rows} & {
        "safe_to_spend", "projected_balances", "paydays"}
    assert "Private" not in str(shared_rows)
    owner_rows = client(session, owner).get("/api/v1/integration/items").json()["items"]
    rows = {item["family"]: item for item in owner_rows}
    assert rows["safe_to_spend"]["value"] == "100.00"
    assert rows["paydays"]["value"] == 7
    assert rows["paydays"]["id"] == f"employment:{employment.id}"


def test_discovery_paginates_without_repeating_or_hiding_later_families(session, household):
    account(session, household)
    api = client(session)
    all_items = api.get("/api/v1/integration/items").json()["items"]
    first = api.get("/api/v1/integration/items?limit=1").json()
    second = api.get("/api/v1/integration/items?limit=1&offset=1").json()
    assert first["has_more"] is True
    assert first["items"] == all_items[:1]
    assert second["items"] == all_items[1:2]
    assert second["items"] != first["items"]
    tail = api.get(f"/api/v1/integration/items?offset={len(all_items)}").json()
    assert tail == {"items": [], "has_more": False}


def test_private_plans_stay_hidden_and_zero_plan_percent_is_unknown(session, household):
    account(session, household)
    owner = UserProfile(household_id=household.id, display_name="Synthetic owner")
    session.add(owner)
    session.flush()
    for private in (False, True):
        session.add_all([
            Budget(household_id=household.id, owner_profile_id=owner.id if private else None,
                   name="Private plan" if private else "Shared zero plan", status="approved",
                   period_type="custom", start_date=date.today(), end_date=date.today()),
            SeasonalEvent(household_id=household.id, type="synthetic", year=date.today().year,
                          owner_profile_id=owner.id if private else None, budget_pence=0,
                          name="Private season" if private else "Shared zero season"),
        ])
    session.flush()
    response = client(session).get("/api/v1/integration/items").json()
    assert "Private" not in str(response)
    rows = response["items"]
    assert not {row["family"] for row in rows} & {"budget_usage", "seasonal_usage"}
    assert next(row for row in rows if row["family"] == "budget_remaining")["value"] == "0.00"


def test_goal_money_threshold_uses_real_contributions_not_opening_balance(session, household):
    shared = account(session, household)
    goal = SavingsGoal(household_id=household.id, account_id=shared.id, name="Synthetic goal",
                      goal_type="general", target_pence=100_000, start_date=date.today())
    transfer = Transfer(household_id=household.id)
    session.add_all([goal, transfer])
    session.flush()
    session.add(Transaction(account_id=shared.id, date=date.today(), amount_pence=12_500,
                            payee="Synthetic contribution", transfer_id=transfer.id,
                            status="cleared"))
    session.flush()
    items = client(session).get("/api/v1/integration/items").json()["items"]
    funded = next(item for item in items if item["family"] == "goal_funding")
    assert funded["value"] == "125.00"
    assert funded["id"] == f"goal:{goal.id}"


def test_goal_dates_enable_arbitrary_native_lead_time_and_omit_unknown_dates(session, household):
    shared = account(session, household)
    goal = SavingsGoal(household_id=household.id, account_id=shared.id, name="Dated synthetic goal",
                      goal_type="general", target_pence=100_000, start_date=date.today(),
                      target_date=date.today() + timedelta(days=7))
    undated = SavingsGoal(household_id=household.id, account_id=shared.id,
                         name="Undated synthetic goal", goal_type="general", target_pence=100_000,
                         start_date=date.today())
    session.add_all([goal, undated])
    session.flush()
    items = client(session).get("/api/v1/integration/items").json()["items"]
    dates = [item for item in items if item["family"] == "goal_dates"]
    assert len(dates) == 1
    assert dates[0]["value"] == 7
    assert dates[0]["id"] == f"goal:{goal.id}:days"


def test_budget_metrics_keep_archived_account_history(session, household):
    archived = account(session, household, archived=True)
    category = Category(household_id=household.id, name="Synthetic category")
    budget = Budget(household_id=household.id, name="Synthetic budget", status="approved",
                    period_type="custom", start_date=date.today(), end_date=date.today())
    session.add_all([category, budget])
    session.flush()
    session.add_all([
        BudgetLine(budget_id=budget.id, category_id=category.id, side="expense",
                   original_pence=10_000),
        Transaction(account_id=archived.id, date=date.today(), amount_pence=-2_500,
                    payee="Synthetic historic expense", category_id=category.id, status="cleared"),
    ])
    session.flush()
    items = client(session).get("/api/v1/integration/items").json()["items"]
    assert next(item for item in items if item["family"] == "budget_remaining")["value"] == "75.00"
    assert not any(item["family"] == "accounts" for item in items)


def test_seasonal_days_use_only_explicit_persisted_date(session, household):
    due = date.today() + timedelta(days=7)
    event = SeasonalEvent(household_id=household.id, type="dated", year=due.year,
                          name="Synthetic dated occasion", budget_pence=0, event_date=due)
    undated = SeasonalEvent(household_id=household.id, type="undated", year=due.year,
                            name="Synthetic unknown occasion", budget_pence=0)
    session.add_all([event, undated])
    session.flush()
    session.expire_all()
    items = client(session).get("/api/v1/integration/items").json()["items"]
    dates = [item for item in items if item["family"] == "seasonal_dates"]
    assert len(dates) == 1
    assert dates[0]["value"] == 7
    assert dates[0]["id"] == f"seasonal:{event.id}:days"


def test_forecast_window_changes_only_its_canonical_horizon(session, household):
    shared = account(session, household)
    session.add(ExpectedMovement(
        household_id=household.id, account_id=shared.id, source_type="manual", source_id=1,
        expected_date=date.today() + timedelta(days=30), amount_pence=-15_000,
        label="Synthetic later obligation", confidence="scheduled"))
    session.flush()
    api = client(session)
    shorter = api.get("/api/v1/integration/items?forecast_days=7").json()["items"]
    assert next(row for row in shorter if row["family"] == "safe_to_spend")["value"] == "100.00"
    default = api.get("/api/v1/integration/items").json()["items"]
    assert next(row for row in default if row["family"] == "safe_to_spend")["value"] == "-50.00"
    assert api.get("/api/v1/integration/items?forecast_days=0").status_code == 422
    assert api.get("/api/v1/integration/items?forecast_days=732").status_code == 422
