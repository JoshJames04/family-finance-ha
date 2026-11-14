"""Historical personal cycles and calendar household views use synthetic data."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import (
    AppMeta,
    Budget,
    ExpectedMatch,
    ExpectedMovement,
    Transaction,
    TransferRule,
)


def post(client, path, body):
    response = client.post(f"/api/v1/{path}", json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def setup(tmp_path):
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"})
    with TestClient(create_app(settings)) as client:
        household = post(client, "households", {"name": "Synthetic cycles"})
        client.headers["X-Household-Id"] = str(household["id"])
        admin = post(client, "profiles", {"display_name": "Synthetic admin", "role": "admin"})
        client.headers["X-Profile-Id"] = str(admin["id"])
        owner = post(client, "profiles", {"display_name": "Synthetic owner", "role": "member"})
        other = post(client, "profiles", {"display_name": "Synthetic other", "role": "member"})
        viewer = post(client, "profiles", {"display_name": "Synthetic viewer", "role": "viewer"})
        account = post(client, "accounts", {
            "name": "Synthetic personal", "owner_profile_id": owner["id"], "type": "personal",
            "opening_balance": 10000, "opening_date": "2025-01-01",
        })
        employment = post(client, "employments", {
            "profile_id": owner["id"], "employer": "Synthetic employer", "contracted_hours": 35,
            "paid_into_account_id": account["id"], "start_date": "2025-01-01",
            "payday_rule": json.dumps({"kind": "fixed_day", "day": 28, "adjust": "previous"}),
        })
        category = post(client, "categories", {"name": "Synthetic food"})
        budget = post(client, "budgets", {
            "name": "November plan", "owner_profile_id": owner["id"], "period_type": "monthly",
            "start_date": "2025-11-01", "end_date": "2025-11-30",
        })
        line = post(client, f"budgets/{budget['id']}/lines", {
            "category_id": category["id"], "side": "expense", "amount": 8000,
        })
        response = client.post(f"/api/v1/budgets/{budget['id']}/approve")
        assert response.status_code == 200, response.text
        for day, amount in [("2025-10-27", -1000), ("2025-10-28", 30000),
                            ("2025-10-28", -2000), ("2025-11-27", -3000),
                            ("2025-11-28", -4000)]:
            post(client, "transactions", {"account_id": account["id"], "date": day,
                "amount": amount, "payee": "Synthetic receipt" if amount > 0 else "Synthetic shop",
                "category_id": None if amount > 0 else category["id"]})
        yield client, owner, other, viewer, employment, budget, line


def test_personal_cycle_bounds_income_and_original_history_are_read_only(setup):
    client, owner, _, _, employment, budget, line = setup
    url = f"/api/v1/budgets/{budget['id']}"
    headers = {"X-Profile-Id": str(owner["id"])}
    with Session(client.app.state.engine) as session:
        before_dates = list(session.execute(select(Transaction.id, Transaction.date)))
        before_meta = list(session.execute(select(AppMeta.key, AppMeta.value)))
    detail = client.get(url, params={"basis": "pay_cycle"}, headers=headers)
    assert detail.status_code == 200, detail.text
    period = detail.json()["period"]
    assert period["start"] == "2025-10-28"
    assert period["end"] == "2025-11-27"
    assert period["employment_id"] == employment["id"]
    actual = client.get(url + "/actuals", params={"basis": "pay_cycle"}, headers=headers).json()
    assert actual["spent_pence"] == 5000
    assert actual["income_received_pence"] == 30000
    assert sum(item["amount_pence"] for item in actual["income_items"]) == 30000
    assert actual["income_items"][0]["category_id"] is None
    assert actual["opening_funds_pence"] == 9000
    assert actual["lines"][str(line["id"])]["effective_pence"] == 8000
    # Existing calendar contracts remain available to reports/dashboard consumers.
    assert client.get(url + "/actuals", headers=headers).json()["spent_pence"] == 7000
    with Session(client.app.state.engine) as session:
        stored = session.get(Budget, budget["id"])
        assert stored.start_date.isoformat() == "2025-11-01"
        assert stored.end_date.isoformat() == "2025-11-30"
        assert stored.status.value == "approved"
        assert list(session.execute(select(Transaction.id, Transaction.date))) == before_dates
        assert list(session.execute(select(AppMeta.key, AppMeta.value))) == before_meta


def test_all_keeps_calendar_actuals_and_private_resources_stay_private(setup):
    client, owner, other, viewer, _, budget, _ = setup
    combined = client.get("/api/v1/budgets/reports/combined", params={
        "start": "2025-11-01", "end": "2025-11-30"})
    assert combined.status_code == 200, combined.text
    assert combined.json()["actuals"]["period"]["basis"] == "calendar"
    assert combined.json()["actuals"]["spent_pence"] == 7000
    assert combined.json()["actuals"]["opening_funds_pence"] == 37000
    for actor in (other, viewer):
        assert client.get(f"/api/v1/budgets/{budget['id']}/actuals?basis=pay_cycle",
                          headers={"X-Profile-Id": str(actor["id"])}).status_code == 404
        assert client.get(f"/api/v1/budgets/pay-cycle-settings?owner_profile_id={owner['id']}",
                          headers={"X-Profile-Id": str(actor["id"])}).status_code == 404


def test_anchor_selection_requires_own_scope_and_valid_employment(setup):
    client, owner, other, viewer, employment, _, _ = setup
    path = "/api/v1/budgets/pay-cycle-settings"
    body = {"owner_profile_id": owner["id"], "employment_id": employment["id"]}
    result = client.put(path, json=body, headers={"X-Profile-Id": str(owner["id"])})
    assert result.status_code == 200, result.text
    assert result.json()["employment_id"] == employment["id"]
    assert result.json()["options"][0]["employment_id"] == employment["id"]
    for actor in (other, viewer):
        assert client.put(path, json=body,
                          headers={"X-Profile-Id": str(actor["id"])}).status_code in (403, 404)
    assert client.put(path, json={**body, "employment_id": 999999}).status_code in (404, 422)


def test_confirmed_partial_withdrawal_removes_only_internal_part_from_expense_bridge(setup):
    client, _, _, _, employment, _, line = setup
    shared = post(client, "accounts", {"name": "Synthetic joint", "type": "joint",
        "opening_balance": 0, "opening_date": "2025-01-01"})
    budget = post(client, "budgets", {"name": "Shared November", "period_type": "monthly",
        "start_date": "2025-11-01", "end_date": "2025-11-30"})
    expense = post(client, f"budgets/{budget['id']}/lines", {
        "category_id": line["category_id"], "side": "expense", "amount": 1000})
    withdrawal = post(client, "transactions", {"account_id": shared["id"],
        "date": "2025-11-05", "amount": -150, "category_id": line["category_id"],
        "payee": "Synthetic withdrawal with external fee"})
    with Session(client.app.state.engine) as session:
        rule = TransferRule(household_id=int(client.headers["X-Household-Id"]),
            name="Synthetic withdrawal", source_account_id=shared["id"],
            dest_account_id=employment["paid_into_account_id"],
            amount_spec='{"kind":"fixed","pence":125}', frequency="monthly",
            day_of_month=5, start_date=date(2025, 11, 1))
        session.add(rule)
        session.flush()
        movement = ExpectedMovement(household_id=rule.household_id,
            account_id=shared["id"], expected_date=date(2025, 11, 5), amount_pence=-125,
            label="Synthetic withdrawal", confidence="scheduled",
            source_type="transfer_rule", source_id=rule.id)
        session.add(movement)
        session.flush()
        session.add(ExpectedMatch(movement_id=movement.id, transaction_id=withdrawal["id"],
                                  matched_pence=125, manual=True))
        session.commit()
    response = client.get(f"/api/v1/budgets/{budget['id']}/actuals")
    assert response.status_code == 200, response.text
    actual = response.json()
    assert actual["contributions_pence"] == -125
    assert actual["spent_pence"] == 25
    assert actual["lines"][str(expense["id"])]["actual_pence"] == 25
    assert actual["budget_covered_pence"] == 25
    assert sum(item["amount_pence"] for item in actual["spend_items"]) == 25
    assert actual["income_received_pence"] == 0


def test_fixed_member_payday_works_without_employment_and_rejects_conflicting_sources(setup):
    client, owner, other, _, employment, _, _ = setup
    path = "/api/v1/budgets/pay-cycle-settings"
    result = client.put(path, json={"owner_profile_id": other["id"], "payday_day": 11})
    assert result.status_code == 200, result.text
    assert result.json()["payday_day"] == 11
    assert result.json()["employment_id"] is None
    assert result.json()["options"] == []
    budget = post(client, "budgets", {"name": "Fixed payday plan", "period_type": "monthly",
        "owner_profile_id": other["id"], "start_date": "2025-11-01", "end_date": "2025-11-30"})
    view = client.get(f"/api/v1/budgets/{budget['id']}?basis=pay_cycle").json()
    assert view["period"] == {"start": "2025-10-10", "end": "2025-11-10",
                               "basis": "pay_cycle", "employment_id": None, "warning": None}
    invalid = client.put(path, json={"owner_profile_id": owner["id"],
        "employment_id": employment["id"], "payday_day": 11})
    assert invalid.status_code == 422
    assert client.put(path, json={"owner_profile_id": other["id"], "payday_day": 32}
                      ).status_code == 422


def test_viewer_role_cannot_read_even_formerly_owned_private_budget(setup):
    client, _, _, viewer, _, _, _ = setup
    private = post(client, "budgets", {"name": "Former private plan", "period_type": "monthly",
        "owner_profile_id": viewer["id"], "start_date": "2025-11-01", "end_date": "2025-11-30"})
    headers = {"X-Profile-Id": str(viewer["id"])}
    assert client.get(f"/api/v1/budgets/{private['id']}?basis=pay_cycle",
                      headers=headers).status_code == 404
    assert client.get(f"/api/v1/budgets/{private['id']}/actuals?basis=pay_cycle",
                      headers=headers).status_code == 404
    listed = client.get("/api/v1/budgets", headers=headers).json()["items"]
    assert private["id"] not in {item["id"] for item in listed}
