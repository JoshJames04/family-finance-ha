"""Public month bonus API, validation, ownership and transactional rollback."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.settings import Settings
from app.main import create_app
from tests.payroll_helpers import confirmation_payload


@pytest.fixture()
def api(tmp_path):
    with TestClient(create_app(Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as client:
        hh = client.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
        h = {"X-Household-Id": str(hh)}
        owner = client.post("/api/v1/profiles", headers=h, json={"display_name": "Owner"}).json()[
            "id"
        ]
        response = client.post(
            "/api/v1/employments",
            headers=h,
            json={
                "profile_id": owner,
                "employer": "Example",
                "contracted_hours": "37.5",
                "payday_rule": '{"kind":"fixed_day","day":28,"adjust":"previous"}',
                "initial_salary": "60000",
                "initial_salary_effective_date": "2025-01-01",
                "initial_tax_code": "1257L",
                "initial_shares_owned": "250",
            },
        )
        assert response.status_code == 201, response.text
        eid = response.json()["id"]
        yield client, h, f"/api/v1/employments/{eid}", owner


def test_bonus_crud_updates_one_run_and_rejects_locked_changes(api):
    c, h, path, _ = api
    body = {
        "pay_month": "2025-04-01",
        "salary_percentage": "10",
        "share_value": "15",
        "manual_amount": "100",
    }
    response = c.post(path + "/bonuses", headers=h, json=body)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["share_bonus_pence"] == 375000
    assert saved["gross_pence"] == 985000
    run = c.get(path + "/payroll-runs", headers=h).json()[0]
    assert (
        c.patch(
            path + f"/bonuses/{saved['id']}", headers=h, json={**body, "manual_amount": "200"}
        ).status_code
        == 200
    )
    runs = c.get(path + "/payroll-runs", headers=h).json()
    assert len(runs) == 1
    assert runs[0]["gross_pence"] == run["gross_pence"] + 10000
    assert (
        c.post(
            path + f"/payroll-runs/{run['id']}/reconcile",
            headers=h,
            json=confirmation_payload(runs[0]),
        ).status_code
        == 200
    )
    assert c.delete(path + f"/bonuses/{saved['id']}", headers=h).status_code == 409
    assert len(c.get(path + "/bonuses", headers=h).json()) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"pay_month": "2025-04-02"},
        {"pay_month": "9999-12-01"},
        {"pay_month": "0001-01-01"},
        {"share_value": "-1"},
        {"manual_amount": "-1"},
        {"salary_percentage": "NaN"},
        {"salary_percentage": "0.00001"},
        {"manual_amount": "0", "share_value": None},
    ],
)
def test_invalid_bonus_never_creates_payroll(api, change):
    c, h, path, _ = api
    result = c.post(
        path + "/bonuses",
        headers=h,
        json={"pay_month": "2025-04-01", "share_value": "15", **change},
    )
    assert result.status_code in {409, 422}
    assert c.get(path + "/payroll-runs", headers=h).json() == []
    assert c.get(path + "/bonuses", headers=h).json() == []


def test_nonowner_admin_cannot_write_bonus(api):
    c, h, path, _ = api
    admin = c.post(
        "/api/v1/profiles", headers=h, json={"display_name": "Admin", "role": "admin"}
    ).json()["id"]
    result = c.post(
        path + "/bonuses",
        headers={**h, "X-Profile-Id": str(admin)},
        json={"pay_month": "2025-04-01", "manual_amount": "100"},
    )
    assert result.status_code == 403


def test_method_and_holdings_validation(api):
    c, h, path, _ = api
    assert c.patch(path, headers=h, json={"share_payment_method": None}).status_code == 422
    assert (
        c.post(
            path + "/salary-revisions",
            headers=h,
            json={"effective_date": "2025-02-01", "annual_salary": "65000", "shares_owned": "-1"},
        ).status_code
        == 422
    )
    assert (
        c.post(
            path + "/salary-revisions",
            headers=h,
            json={
                "effective_date": "2025-02-01",
                "annual_salary": "65000",
                "shares_owned": "250.00001",
            },
        ).status_code
        == 422
    )


@pytest.mark.parametrize("change", ["shares", "notes", "unchanged"])
def test_nonpay_revision_save_preserves_forecast_rows_and_bonus_snapshots(api, change):
    c, h, path, _ = api
    bonus = c.post(
        path + "/bonuses",
        headers=h,
        json={
            "pay_month": "2025-04-01",
            "share_value": "15",
        },
    )
    assert bonus.status_code == 201, bonus.text
    before = c.get(path + "/payroll-runs", headers=h).json()
    revision = c.get(path + "/salary-revisions", headers=h).json()[0]
    payroll_sql = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if "payroll_run" in statement.lower():
            payroll_sql.append(statement)

    event.listen(c.app.state.engine, "before_cursor_execute", capture)
    try:
        response = c.patch(
            path + f"/salary-revisions/{revision['id']}",
            headers=h,
            json={
                "effective_date": revision["effective_date"],
                "annual_salary": str(revision["annual_salary_pence"] / 100),
                "shares_owned": "300" if change == "shares" else revision["shares_owned"],
                "notes": "Synthetic note" if change == "notes" else revision["notes"],
            },
        )
    finally:
        event.remove(c.app.state.engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert not payroll_sql  # Constant work: no payroll scan or rebuild on this save path.
    assert Decimal(response.json()["shares_owned"]) == Decimal(
        "300" if change == "shares" else revision["shares_owned"]
    )
    assert c.get(path + "/payroll-runs", headers=h).json() == before
    assert c.get(path + "/bonuses", headers=h).json() == [bonus.json()]
    if change == "shares":
        later = c.post(
            path + "/bonuses",
            headers=h,
            json={
                "pay_month": "2025-04-01",
                "share_value": "15",
            },
        )
        assert later.status_code == 201, later.text
        assert Decimal(later.json()["shares_owned"]) == Decimal("300")
        assert later.json()["share_bonus_pence"] == 450000


@pytest.mark.parametrize("change", ["salary", "date"])
def test_pay_revision_save_still_automatically_rebuilds_forecast(api, change):
    c, h, path, _ = api
    response = c.post(
        path + "/bonuses",
        headers=h,
        json={
            "pay_month": "2025-04-01",
            "manual_amount": "100",
        },
    )
    assert response.status_code == 201, response.text
    before = c.get(path + "/payroll-runs", headers=h).json()[0]
    revision = c.get(path + "/salary-revisions", headers=h).json()[0]
    response = c.patch(
        path + f"/salary-revisions/{revision['id']}",
        headers=h,
        json={
            "annual_salary": "72000" if change == "salary" else "60000",
            "effective_date": "2025-01-02" if change == "date" else "2025-01-01",
            "shares_owned": "300",
        },
    )
    assert response.status_code == 200, response.text
    after = c.get(path + "/payroll-runs", headers=h).json()[0]
    assert after["id"] == before["id"]
    assert not after["forecast_stale"]
    assert [r["id"] for r in after["components"]] != [r["id"] for r in before["components"]]
    if change == "salary":
        assert after["gross_pence"] > before["gross_pence"]
