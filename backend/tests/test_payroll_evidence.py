"""Synthetic recovery evidence must never rewrite recorded payroll."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import (
    AuditEvent,
    Employment,
    PayrollComponent,
    PayrollComponentKind,
    PayrollRun,
    PayrollRunStatus,
)


@pytest.fixture
def api(tmp_path):
    with TestClient(create_app(Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as client:
        hh = client.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
        headers = {"X-Household-Id": str(hh)}
        owner = client.post("/api/v1/profiles", headers=headers,
                            json={"display_name": "Owner"}).json()["id"]
        headers["X-Profile-Id"] = str(owner)
        emp = client.post("/api/v1/employments", headers=headers, json={
            "profile_id": owner, "employer": "Synthetic", "deduction_mode": "paye",
            "contracted_hours": "37.5",
        }).json()["id"]
        with Session(client.app.state.engine) as session:
            run = PayrollRun(employment_id=emp, period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 30), pay_date=date(2026, 4, 28),
                status="reconciled", tax_year="2026-2027", rule_set_version=1,
                gross_pence=300000, net_pence=250000)
            session.add(run)
            session.flush()
            for kind in PayrollComponentKind:
                value = {"base_salary": 280000, "overtime": 20000, "tax": -40000,
                         "ni": -10000}.get(kind.value, 0)
                run.components.append(PayrollComponent(kind=kind, label=kind.value,
                    forecast_pence=value, actual_pence=value))
            # Preserve separate rates, including their identities and original forecasts.
            run.components.append(PayrollComponent(kind=PayrollComponentKind.overtime,
                label="Overtime second rate", forecast_pence=0, actual_pence=0))
            session.commit()
            rid = run.id
        path = f"/api/v1/employments/{emp}/payroll-runs/{rid}/verify-inputs"
        body = dict(payslip_gross_pence=300000, payslip_net_pence=250000,
                    taxable_pence=300000, niable_pence=300000,
                    ytd_taxable_pence=300000, ytd_tax_pence=40000, source_sha256="a" * 64)
        yield client, headers, path, body, rid


def snapshot(client, rid):
    with Session(client.app.state.engine) as session:
        run = session.get(PayrollRun, rid)
        return [(c.id, c.kind, c.label, c.forecast_pence, c.actual_pence)
                for c in run.components], run.gross_pence, run.net_pence, run.status


def audits(client):
    with Session(client.app.state.engine) as session:
        return [a.payload for a in session.scalars(select(AuditEvent).where(
            AuditEvent.action == "verify_inputs"))]


def test_owner_attach_preserves_components_and_is_idempotent(api):
    from app.persistence.models import Account, ExpectedMatch, ExpectedMovement, Transaction

    client, headers, path, body, rid = api
    before = snapshot(client, rid)
    with Session(client.app.state.engine) as session:
        account = Account(household_id=int(headers["X-Household-Id"]), name="Synthetic bank",
                          type="current", opening_date=date(2026, 1, 1))
        session.add(account)
        session.flush()
        transaction = Transaction(account_id=account.id, date=date(2026, 4, 28),
                                  amount_pence=250000, payee="Synthetic salary")
        movement = ExpectedMovement(household_id=account.household_id, account_id=account.id,
            expected_date=date(2026, 4, 28), amount_pence=250000, label="Synthetic salary",
            confidence="scheduled", source_type="payroll_run", source_id=rid)
        session.add_all([transaction, movement])
        session.flush()
        match = ExpectedMatch(movement_id=movement.id, transaction_id=transaction.id,
                              matched_pence=250000, manual=True)
        session.add(match)
        session.commit()
        bank_ids = match.id, movement.id, transaction.id
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["run"]["verified_taxable_pence"] == 300000
    assert response.json()["refresh"]["issues"]  # Missing setup is surfaced, evidence saved.
    assert snapshot(client, rid) == before
    assert client.post(path, headers=headers, json=body).status_code == 200
    assert len(audits(client)) == 1
    assert '"source_sha256"' in audits(client)[0]
    assert "300000" not in audits(client)[0]
    with Session(client.app.state.engine) as session:
        match = session.get(ExpectedMatch, bank_ids[0])
        assert (match.movement_id, match.transaction_id, match.matched_pence, match.manual) == (
            bank_ids[1], bank_ids[2], 250000, True
        )
        assert session.get(ExpectedMovement, bank_ids[1]).amount_pence == 250000
        assert session.get(Transaction, bank_ids[2]).amount_pence == 250000


@pytest.mark.parametrize("change", [
    {"payslip_gross_pence": 300001}, {"payslip_net_pence": 250001},
    {"ytd_tax_pence": None}, {"taxable_pence": None}, {"source_sha256": "invalid"},
])
def test_invalid_evidence_is_atomic_and_unaudited(api, change):
    client, headers, path, body, rid = api
    before = snapshot(client, rid)
    response = client.post(path, headers=headers, json={**body, **change})
    assert response.status_code in {409, 422}, response.text
    assert snapshot(client, rid) == before
    assert not audits(client)


@pytest.mark.parametrize("change", ["partial", "empty", "unsigned", "conflict", "corrected"])
def test_ambiguous_or_changed_history_is_rejected(api, change):
    client, headers, path, body, rid = api
    with Session(client.app.state.engine) as session:
        run = session.get(PayrollRun, rid)
        if change == "partial":
            run.components[0].actual_pence = None
        elif change == "empty":
            run.components.clear()
        elif change == "unsigned":
            tax = next(c for c in run.components if c.kind == PayrollComponentKind.tax)
            tax.actual_pence = 40000
        elif change == "conflict":
            run.verified_taxable_pence = 1
        else:
            run.status = "corrected"
        session.commit()
    before = snapshot(client, rid)
    assert client.post(path, headers=headers, json=body).status_code == 409
    assert snapshot(client, rid) == before
    assert not audits(client)


@pytest.mark.parametrize("identity", ["admin", "viewer", "machine", "anonymous", "household"])
def test_only_identified_owner_may_attach(api, identity):
    client, headers, path, body, rid = api
    if identity in {"admin", "viewer"}:
        profile = client.post("/api/v1/profiles", headers=headers,
            json={"display_name": identity, "role": identity}).json()["id"]
        headers = {**headers, "X-Profile-Id": str(profile)}
    elif identity == "anonymous":
        headers = {k: v for k, v in headers.items() if k != "X-Profile-Id"}
    elif identity == "household":
        other = client.post("/api/v1/households", json={"name": "Other"}).json()["id"]
        headers = {"X-Household-Id": str(other)}
    else:
        async def service(request: Request, call_next):
            request.state.service_authenticated = True
            return await call_next(request)
        client.app.middleware_stack = BaseHTTPMiddleware(client.app.middleware_stack,
                                                        dispatch=service)
    assert client.post(path, headers=headers, json=body).status_code in {403, 404}
    assert not audits(client)


def test_legacy_history_recovery_unblocks_approved_overtime_and_bonus(api, monkeypatch):
    from app.domain import overtime, payroll, payroll_automation, payrun

    class Clock(date):
        @classmethod
        def today(cls):
            return date(2026, 5, 1)

    monkeypatch.setattr(payroll_automation, "date", Clock)
    client, headers, path, body, rid = api
    emp_id = int(path.split("/")[4])
    with Session(client.app.state.engine) as session:
        employment = session.get(Employment, emp_id)
        employment.start_date = date(2026, 4, 1)
        employment.salary_policy = "equal_monthly"
        employment.payday_rule = '{"kind":"fixed_day","day":28}'
        payroll.add_salary_revision(session, employment, date(2026, 4, 1), 3600000)
        payroll.add_tax_code_revision(session, employment, date(2026, 4, 1), "1257L")
        entry = overtime.create_entry(session, employment, work_date=date(2026, 5, 8),
                                      hours=Decimal("4"))
        overtime.submit(entry)
        overtime.approve(entry)
        # Legacy future row has stored totals but no complete earning lines.
        future = PayrollRun(employment_id=emp_id, period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31), pay_date=date(2026, 5, 28),
            tax_year="2026-27", rule_set_version=1, status=PayrollRunStatus.forecast,
            gross_pence=300000, net_pence=250000, forecast_stale=True)
        session.add(future)
        session.flush()
        with pytest.raises(payroll.PayrollError, match="verified taxable"):
            payrun.prior_ytd(session, employment, date(2026, 5, 28))
        blocked = payroll_automation.refresh(session, employment)
        assert blocked["issues"]
        assert future.forecast_stale
        fid = future.id
        session.commit()
    base = f"/api/v1/employments/{emp_id}"
    bonus = {"pay_month": "2026-05-01", "manual_amount": "100"}
    assert client.post(base + "/bonuses", headers=headers, json=bonus).status_code == 409
    before = snapshot(client, rid)
    recovered = client.post(path, headers=headers, json=body)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["refresh"]["updated"] >= 1
    assert snapshot(client, rid) == before
    with Session(client.app.state.engine) as session:
        future = session.get(PayrollRun, fid)
        assert not future.forecast_stale
        overtime_pay = sum(c.forecast_pence for c in future.components
                           if c.kind == PayrollComponentKind.overtime)
        assert overtime_pay > 0
        assert future.gross_pence == 300000 + overtime_pay
    added = client.post(base + "/bonuses", headers=headers, json=bonus)
    assert added.status_code == 201, added.text
    assert snapshot(client, rid) == before


@pytest.mark.parametrize("changed", ["status", "component"])
def test_cached_history_is_reloaded_after_writer_lock(api, changed):
    from app.domain import payroll_evidence
    from app.domain.payroll import PayrollError

    client, headers, path, body, rid = api
    with Session(client.app.state.engine) as cached:
        run = cached.get(PayrollRun, rid)
        employment = run.employment
        assert run.components[0].actual_pence is not None
        with Session(client.app.state.engine) as writer:
            latest = writer.get(PayrollRun, rid)
            if changed == "status":
                latest.status = PayrollRunStatus.corrected
            else:
                latest.components[0].actual_pence = None
            writer.commit()
        with pytest.raises(PayrollError):
            payroll_evidence.verify_inputs(cached, employment, rid, actor="Synthetic", **body)
        cached.rollback()
    assert not audits(client)


def test_independent_gross_can_include_returned_employee_pension(api):
    client, headers, path, body, rid = api
    with Session(client.app.state.engine) as session:
        run = session.get(PayrollRun, rid)
        pension = next(c for c in run.components if c.kind == PayrollComponentKind.pension)
        pension.forecast_pence = pension.actual_pence = 5000
        run.gross_pence = 305000
        run.net_pence = 255000
        session.commit()
    before = snapshot(client, rid)
    result = client.post(path, headers=headers,
        json={**body, "payslip_gross_pence": 305000, "payslip_net_pence": 255000})
    assert result.status_code == 200, result.text
    assert snapshot(client, rid) == before
    assert result.json()["run"]["verified_taxable_pence"] == 300000


def test_legacy_postgross_leave_preserves_printed_gross_and_explicit_taxable(api):
    client, headers, path, body, rid = api
    with Session(client.app.state.engine) as session:
        run = session.get(PayrollRun, rid)
        leave = next(c for c in run.components if c.kind == PayrollComponentKind.unpaid_leave)
        leave.label = "Legacy post-gross deduction"
        leave.forecast_pence = leave.actual_pence = -10000
        run.net_pence = 240000
        session.commit()
    before = snapshot(client, rid)
    result = client.post(path, headers=headers, json={**body,
        "payslip_net_pence": 240000, "taxable_pence": 290000})
    assert result.status_code == 200, result.text
    assert snapshot(client, rid) == before
    assert result.json()["run"]["verified_gross_pence"] == 300000
    assert result.json()["run"]["verified_taxable_pence"] == 290000
