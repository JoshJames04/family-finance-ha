"""Budget aggregation and register deletion use synthetic app databases only."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    with TestClient(
        create_app(Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"}))
    ) as c:
        hid = c.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
        c.headers["X-Household-Id"] = str(hid)
        admin = c.post("/api/v1/profiles", json={"display_name": "A", "role": "admin"}).json()
        c.headers["X-Profile-Id"] = str(admin["id"])
        yield c


def test_template_delete_preserves_applied_budget(client):
    cat = client.post("/api/v1/categories", json={"name": "Food"}).json()
    template = client.post("/api/v1/budgets/templates", json={"name": "Plan"}).json()
    path = f"/api/v1/budgets/templates/{template['id']}"
    assert (
        client.post(
            path + "/lines", json={"category_id": cat["id"], "side": "expense", "amount": 10000}
        ).status_code
        == 201
    )
    budget = client.post(
        "/api/v1/budgets",
        json={
            "name": "Saved",
            "period_type": "custom",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
    ).json()
    bid = budget["id"]
    assert (
        client.post(
            f"/api/v1/budgets/{bid}/apply-template", json={"template_id": template["id"]}
        ).status_code
        == 200
    )
    assert client.delete(path).status_code == 204
    assert client.get(path).status_code == 404
    assert client.get(f"/api/v1/budgets/{bid}").json()["lines"][0]["original_pence"] == 10000
    assert client.delete(path).status_code == 404


def test_tariff_delete_is_household_scoped_and_denies_viewer(client):
    tariff = client.post(
        "/api/v1/energy/tariffs",
        json={"effective_from": "2026-01-01", "unit_rate_millipence": 25000, "standing_charge": 50},
    ).json()
    path = f"/api/v1/energy/tariffs/{tariff['id']}"
    admin = client.headers["X-Profile-Id"]
    viewer = client.post("/api/v1/profiles", json={"display_name": "V", "role": "viewer"}).json()
    assert client.delete(path, headers={"X-Profile-Id": str(viewer["id"])}).status_code == 403
    assert client.delete(path, headers={"X-Household-Id": "9999"}).status_code == 404
    assert client.delete(path, headers={"X-Profile-Id": admin}).status_code == 204
    assert client.get("/api/v1/energy/tariffs").json() == []


def test_combined_budget_counts_shared_and_private_ledger_once(client):
    today = date.today().isoformat()
    owner = int(client.headers["X-Profile-Id"])
    cat = client.post("/api/v1/categories", json={"name": "Food"}).json()
    account = client.post(
        "/api/v1/accounts",
        json={
            "name": "Personal",
            "type": "personal",
            "owner_profile_id": owner,
            "opening_balance": 0,
            "opening_date": today,
        },
    ).json()
    client.post(
        "/api/v1/transactions",
        json={
            "account_id": account["id"],
            "date": today,
            "amount": -5000,
            "payee": "Shop",
            "category_id": cat["id"],
        },
    )
    for scope in [None, owner]:
        b = client.post(
            "/api/v1/budgets",
            json={
                "name": "Plan",
                "owner_profile_id": scope,
                "period_type": "custom",
                "start_date": today,
                "end_date": today,
            },
        ).json()
        client.post(
            f"/api/v1/budgets/{b['id']}/lines",
            json={"category_id": cat["id"], "side": "expense", "amount": 10000},
        )
    response = client.get(f"/api/v1/budgets/reports/combined?start={today}&end={today}")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["detail"]["read_only"] is True
    assert result["detail"]["lines"][0]["effective_pence"] == 20000
    assert result["actuals"]["spent_pence"] == 5000
    assert sum(result["actuals"]["totals"].values()) == 5000
    assert len(result["actuals"]["spend_items"]) == 1
