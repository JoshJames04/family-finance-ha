"""Regression coverage for the settled piecework and payroll surfaces."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(
            Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as value:
        yield value


def _piecework(client: TestClient) -> tuple[dict[str, str], int]:
    household = client.post("/api/v1/households", json={"name": "H"}).json()
    headers = {"X-Household-Id": str(household["id"])}
    profile = client.post(
        "/api/v1/profiles", json={"display_name": "J"}, headers=headers).json()
    employment = client.post(
        "/api/v1/employments",
        json={
            "profile_id": profile["id"], "employer": "Piecework Co",
            "contracted_hours": 20, "employment_type": "piecework",
            "deduction_mode": "gross_only", "start_date": "2025-01-01",
            "payday_rule": (
                '{"kind":"nightly","day":null,"weekday":null,'
                '"nth":null,"adjust":"none"}'),
        }, headers=headers).json()
    return headers, employment["id"]


def test_delivery_schema_charges_edit_and_delete_guard(
        client: TestClient) -> None:
    headers, emp_id = _piecework(client)
    response = client.post(
        f"/api/v1/employments/{emp_id}/deliveries",
        json={
            "work_date": "2025-01-03", "hours": "4",
            "hourly_rate": "12.50", "tips": "2.00",
            "deliveries": 0, "charges": [
                {"amount": "3.50", "label": "Large delivery"},
                {"amount": "1.25", "label": "Distance"},
            ],
        }, headers=headers)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["pay_pence"] == 5675
    assert row["imported_total_pence"] is None
    assert len(row["charges"]) == 2

    edited = client.patch(
        f"/api/v1/employments/{emp_id}/deliveries/{row['id']}",
        json={
            "work_date": "2025-01-03", "hours": "5",
            "hourly_rate": "12.50", "tips": 0,
            "deliveries": 0, "charges": [{"amount": "2.00"}],
        }, headers=headers)
    assert edited.status_code == 200, edited.text
    assert edited.json()["pay_pence"] == 6450
    assert client.delete(
        f"/api/v1/employments/{emp_id}", headers=headers).status_code == 409


def test_delivery_entry_feeds_nightly_payroll_run(
        client: TestClient) -> None:
    headers, emp_id = _piecework(client)
    created = client.post(
        f"/api/v1/employments/{emp_id}/deliveries",
        json={
            "work_date": "2025-01-03", "hours": "4",
            "hourly_rate": "12.50", "deliveries": 0,
        }, headers=headers)
    assert created.status_code == 201, created.text
    runs = client.get(
        f"/api/v1/employments/{emp_id}/payroll-runs",
        headers=headers).json()
    assert len(runs) == 1
    assert runs[0]["period_start"] == "2025-01-03"
    assert runs[0]["pay_date"] == "2025-01-03"
    assert runs[0]["gross_pence"] == 5000
    assert runs[0]["status"] == "forecast"

    edited = client.patch(
        f"/api/v1/employments/{emp_id}/deliveries/{created.json()['id']}",
        json={
            "work_date": "2025-01-03", "hours": "6",
            "hourly_rate": "12.50", "deliveries": 0,
        }, headers=headers)
    assert edited.status_code == 200, edited.text
    runs = client.get(
        f"/api/v1/employments/{emp_id}/payroll-runs",
        headers=headers).json()
    assert len(runs) == 1          # refreshed, not duplicated
    assert runs[0]["gross_pence"] == 7500

    deleted = client.delete(
        f"/api/v1/employments/{emp_id}/deliveries/{created.json()['id']}",
        headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert client.get(
        f"/api/v1/employments/{emp_id}/payroll-runs",
        headers=headers).json() == []


def test_nightly_prepare_warns_for_paye_without_creating_monthly_runs(
        client: TestClient) -> None:
    headers, emp_id = _piecework(client)
    client.patch(
        f"/api/v1/employments/{emp_id}",
        json={"deduction_mode": "paye"}, headers=headers)
    result = client.post(
        "/api/v1/employments/prepare-year",
        json={"year": 2025}, headers=headers)
    assert result.status_code == 201, result.text
    assert any("weekly aggregation" in warning
               for warning in result.json()["warnings"])
    assert client.get(
        f"/api/v1/employments/{emp_id}/payroll-runs",
        headers=headers).json() == []


def test_replacement_create_is_atomic_and_end_dates_selected_prior(
        client: TestClient) -> None:
    household = client.post("/api/v1/households", json={"name": "H"}).json()
    headers = {"X-Household-Id": str(household["id"])}
    profile = client.post(
        "/api/v1/profiles", json={"display_name": "J"}, headers=headers).json()
    prior = client.post(
        "/api/v1/employments",
        json={"profile_id": profile["id"], "employer": "Old",
              "contracted_hours": 20, "start_date": "2025-01-01"},
        headers=headers).json()
    response = client.post(
        "/api/v1/employments",
        json={
            "profile_id": profile["id"], "employer": "New",
            "contracted_hours": 20, "start_date": "2025-06-01",
            "initial_salary": "30000",
            "initial_salary_effective_date": "2025-06-01",
            "replaces_employment_id": prior["id"],
            "replacement_end_date": "2025-05-31",
            "replacement_confirmed": True,
        }, headers=headers)
    assert response.status_code == 201, response.text
    assert client.get(
        f"/api/v1/employments/{prior['id']}", headers=headers).json()["end_date"] == "2025-05-31"
    revisions = client.get(
        f"/api/v1/employments/{response.json()['id']}/salary-revisions",
        headers=headers).json()
    assert revisions[0]["annual_salary_pence"] == 3_000_000

    failed = client.post(
        "/api/v1/employments",
        json={
            "profile_id": profile["id"], "employer": "Broken",
            "contracted_hours": 20, "start_date": "2025-06-01",
            "replaces_employment_id": prior["id"],
            "replacement_end_date": "2025-06-01",
            "replacement_confirmed": True,
        }, headers=headers)
    assert failed.status_code == 422
