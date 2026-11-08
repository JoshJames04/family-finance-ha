"""Payslip upload, privacy and explicit confirmation regressions."""

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


def _run(client: TestClient) -> tuple[dict[str, str], int, int]:
    household = client.post("/api/v1/households", json={"name": "H"}).json()
    headers = {"X-Household-Id": str(household["id"])}
    profile = client.post(
        "/api/v1/profiles", json={"display_name": "J"}, headers=headers).json()
    employment = client.post(
        "/api/v1/employments",
        json={"profile_id": profile["id"], "employer": "Acme",
              "contracted_hours": 37.5, "start_date": "2025-01-01"},
        headers=headers).json()
    client.post(
        f"/api/v1/employments/{employment['id']}/salary-revisions",
        json={"effective_date": "2025-01-01", "annual_salary": "36000"},
        headers=headers)
    client.post(
        f"/api/v1/employments/{employment['id']}/tax-code-revisions",
        json={"effective_date": "2025-01-01", "tax_code": "1257L"},
        headers=headers)
    run = client.post(
        f"/api/v1/employments/{employment['id']}/payroll-runs",
        json={"period_start": "2025-04-01", "period_end": "2025-04-30",
              "pay_date": "2025-04-28"}, headers=headers).json()
    return headers, employment["id"], run["id"]


def _upload(client: TestClient) -> tuple[dict[str, str], int, int, str, int]:
    headers, emp_id, run_id = _run(client)
    path = f"/api/v1/employments/{emp_id}/payroll-runs/{run_id}/payslips"
    row = client.post(
        path, files={"file": ("statement.pdf", b"%PDF-test",
                              "application/pdf")}, headers=headers).json()
    return headers, emp_id, run_id, path, row["id"]


def test_payslip_upload_review_confirm_and_download(
        client: TestClient) -> None:
    headers, emp_id, run_id = _run(client)
    path = f"/api/v1/employments/{emp_id}/payroll-runs/{run_id}/payslips"
    uploaded = client.post(
        path, files={"file": ("statement.pdf", b"%PDF-not-a-real-document",
                              "application/pdf")}, headers=headers)
    assert uploaded.status_code == 201, uploaded.text
    row = uploaded.json()
    assert row["original_name"] == "statement.pdf"
    assert client.post(
        path, files={"file": ("statement.txt", b"plain", "text/plain")},
        headers=headers).status_code == 415

    reviewed = client.patch(
        f"{path}/{row['id']}",
        json={"components": {"base_salary": 1000},
              "warnings": ["manual verification"],
              "overtime_lines": [{
                  "id": "line-1", "hours": "2", "printed_rate_pence": 1500,
              }]}, headers=headers)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "needs_review"
    assert reviewed.json()["overtime_findings"][0]["line_id"] == "line-1"
    resolved = client.post(
        f"{path}/{row['id']}/overtime-findings/line-1/resolve",
        json={"action": "keep_logged", "reason": "Employer owes hours"},
        headers=headers)
    assert resolved.status_code == 200, resolved.text
    assert client.post(
        f"{path}/{row['id']}/confirm", json={"actuals": {}},
        headers=headers).status_code == 422
    confirmed = client.post(
        f"{path}/{row['id']}/confirm",
        json={"actuals": {"base_salary": 1000}}, headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    downloaded = client.get(f"{path}/{row['id']}/download", headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-not-a-real-document"


def test_confirmed_review_components_are_immutable(client: TestClient) -> None:
    headers, emp_id, run_id, path, payslip_id = _upload(client)
    confirmed = client.post(
        f"{path}/{payslip_id}/confirm",
        json={"actuals": {"base_salary": 1000}}, headers=headers)
    assert confirmed.status_code == 200
    assert client.patch(
        f"{path}/{payslip_id}", json={"components": {"base_salary": 2000}},
        headers=headers).status_code == 409


def test_resolved_finding_cannot_be_erased_by_empty_review(
        client: TestClient) -> None:
    headers, emp_id, run_id, path, payslip_id = _upload(client)
    reviewed = client.patch(
        f"{path}/{payslip_id}",
        json={"overtime_lines": [{
            "id": "line-1", "hours": "2", "printed_rate_pence": 1500,
        }]}, headers=headers)
    assert reviewed.status_code == 200
    resolved = client.post(
        f"{path}/{payslip_id}/overtime-findings/line-1/resolve",
        json={"action": "keep_logged", "reason": "owed"},
        headers=headers)
    assert resolved.status_code == 200
    assert client.patch(
        f"{path}/{payslip_id}", json={"overtime_lines": []},
        headers=headers).status_code == 409


def test_naive_snooze_date_is_rejected(client: TestClient) -> None:
    headers, emp_id, run_id, path, payslip_id = _upload(client)
    reviewed = client.patch(
        f"{path}/{payslip_id}",
        json={"overtime_lines": [{
            "id": "line-1", "hours": "2", "printed_rate_pence": 1500,
        }]}, headers=headers)
    assert reviewed.status_code == 200
    response = client.post(
        f"{path}/{payslip_id}/overtime-findings/line-1/resolve",
        json={"action": "snooze", "reason": "later",
              "snooze_until": "2099-01-01T00:00:00"},
        headers=headers)
    assert response.status_code == 422


def test_finding_resolution_is_household_scoped(client: TestClient) -> None:
    headers, emp_id, run_id, path, payslip_id = _upload(client)
    client.patch(
        f"{path}/{payslip_id}",
        json={"overtime_lines": [{
            "id": "line-1", "hours": "2", "printed_rate_pence": 1500,
        }]}, headers=headers)
    other = client.post("/api/v1/households", json={"name": "Other"}).json()
    other_headers = {"X-Household-Id": str(other["id"])}
    response = client.post(
        f"{path}/{payslip_id}/overtime-findings/line-1/resolve",
        json={"action": "manual", "reason": "not mine"},
        headers=other_headers)
    assert response.status_code == 404


def test_add_missing_creates_draft_and_repeats_idempotently(
        client: TestClient) -> None:
    headers, emp_id, run_id = _run(client)
    overtime = client.post(
        f"/api/v1/employments/{emp_id}/overtime-entries",
        json={"work_date": "2025-04-10", "hours": "8",
              "multiplier_bps": 15000, "day_type": "weekday"},
        headers=headers).json()
    printed_rate = round(overtime["hourly_rate_pence"] * 1.5)
    client.post(
        f"/api/v1/employments/{emp_id}/overtime-entries/{overtime['id']}/submit",
        headers=headers)
    client.post(
        f"/api/v1/employments/{emp_id}/overtime-entries/{overtime['id']}/approve",
        headers=headers)
    path = f"/api/v1/employments/{emp_id}/payroll-runs/{run_id}/payslips"
    row = client.post(
        path, files={"file": ("statement.pdf", b"%PDF-add",
                              "application/pdf")}, headers=headers).json()
    reviewed = client.patch(
        f"{path}/{row['id']}",
        json={"overtime_lines": [{
            "id": "line-1", "hours": "10",
            "printed_rate_pence": printed_rate,
        }]}, headers=headers).json()
    finding = reviewed["overtime_findings"][0]
    assert finding["status"] == "paid-extra"
    resolved = client.post(
        f"{path}/{row['id']}/overtime-findings/line-1/resolve",
        json={"action": "add_missing", "reason": "Confirm missing hours",
              "work_date": "2025-04-10", "day_type": "weekday"},
        headers=headers)
    assert resolved.status_code == 200, resolved.text
    entry_id = resolved.json()["overtime_findings"][0]["resolution"]["created_entry_id"]
    repeated = client.post(
        f"{path}/{row['id']}/overtime-findings/line-1/resolve",
        json={"action": "add_missing", "reason": "repeat",
              "work_date": "2025-04-10", "day_type": "weekday"},
        headers=headers)
    assert repeated.status_code == 409
    entries = client.get(
        f"/api/v1/employments/{emp_id}/overtime-entries",
        headers=headers).json()
    created = [entry for entry in entries if entry["id"] == entry_id]
    assert len(created) == 1
    assert created[0]["hours"] == "2.00"
    assert created[0]["hourly_rate_pence"] == overtime["hourly_rate_pence"]
    assert created[0]["pay_pence"] == round(
        2 * overtime["hourly_rate_pence"] * 1.5)


def test_parse_extracts_labelled_totals_and_ot_lines() -> None:
    from app.domain.payslips import _parse
    out, _warnings = _parse(
        "ACME LTD\n"
        "Gross Pay            3,200.00\n"
        "PAYE Income Tax        512.40\n"
        "National Insurance     204.96\n"
        "Student Loan            88.00\n"
        "Pension                160.00\n"
        "Net Pay              2,234.64\n"
        "Overtime  10.0 hrs @ 29.50\n")
    components = out["components"]
    assert components["tax"] == -51_240
    assert components["ni"] == -20_496
    assert components["student_loan"] == -8_800
    assert components["pension"] == -16_000
    assert out["gross_pence"] == 320_000
    assert out["net_pence"] == 223_464
    assert out["overtime_lines"] == [
        {"id": "line-1", "hours": "10.0", "printed_rate_pence": 2950}]


def test_parse_handles_free_text_noise() -> None:
    from app.domain.payslips import _parse
    out, warnings = _parse("no numbers here at all\n")
    assert out["components"] == {}
    assert warnings
