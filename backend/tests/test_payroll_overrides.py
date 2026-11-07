"""Issue #85 — run-scoped manual payroll overrides: audited, visible
as distinct lines, never retro-applied; reconciled runs need re-open."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import payroll as dom
from app.domain import payrun
from app.domain.payroll import PayrollError
from app.main import create_app
from app.persistence.models import (
    Employment,
    Household,
    PayrollComponentKind,
    PayrollOverride,
    PayrollRun,
    PayrollRunStatus,
    UserProfile,
)

APR_S, APR_E, PAY = date(2025, 4, 1), date(2025, 4, 30), date(2025, 4, 28)


@pytest.fixture()
def emp(session: Session, household: Household) -> Employment:
    p = UserProfile(household_id=household.id, display_name="J")
    session.add(p)
    session.flush()
    e = Employment(household_id=household.id, profile_id=p.id,
                   employer="Acme", jurisdiction="england_wales",
                   contracted_hours=Decimal("37.5"),
                   ni_category="A")
    session.add(e)
    session.flush()
    dom.add_salary_revision(session, e, date(2025, 1, 1), 3_600_000)
    dom.add_tax_code_revision(session, e, date(2025, 1, 1), "1257L")
    return e


@pytest.fixture()
def run(session: Session, emp: Employment) -> PayrollRun:
    return payrun.generate(session, emp, start=APR_S, end=APR_E,
                           pay_date=PAY)


class TestApplyOverride:
    def test_earnings_delta_moves_gross_and_net(
            self, session: Session, run: PayrollRun) -> None:
        g0, n0 = run.gross_pence, run.net_pence
        payrun.apply_override(
            session, run, kind=PayrollComponentKind.bonus,
            delta_pence=10_000, reason="Spot award", actor="admin")
        assert run.gross_pence == g0 + 10_000
        assert run.net_pence == n0 + 10_000

    def test_deduction_delta_moves_net_only(
            self, session: Session, run: PayrollRun) -> None:
        g0, n0 = run.gross_pence, run.net_pence
        payrun.apply_override(
            session, run, kind=PayrollComponentKind.tax,
            delta_pence=-5_000, reason="HMRC adjustment",
            actor="admin")
        assert run.gross_pence == g0
        assert run.net_pence == n0 - 5_000

    def test_visible_as_distinct_component_line(
            self, session: Session, run: PayrollRun) -> None:
        payrun.apply_override(
            session, run, kind=PayrollComponentKind.bonus,
            delta_pence=7_500, reason="Referral", actor="admin")
        labels = [c.label for c in run.components]
        assert "Manual override — Referral" in labels

    def test_reason_and_actor_recorded(
            self, session: Session, run: PayrollRun) -> None:
        ov = payrun.apply_override(
            session, run, kind=PayrollComponentKind.tax,
            delta_pence=1_000, reason="Rounding", actor="sam")
        assert ov.reason == "Rounding"
        assert ov.actor == "sam"

    def test_zero_delta_rejected(self, session: Session,
                                 run: PayrollRun) -> None:
        with pytest.raises(PayrollError):
            payrun.apply_override(
                session, run, kind=PayrollComponentKind.bonus,
                delta_pence=0, reason="No-op", actor="admin")

    def test_empty_reason_rejected(self, session: Session,
                                   run: PayrollRun) -> None:
        with pytest.raises(PayrollError):
            payrun.apply_override(
                session, run, kind=PayrollComponentKind.bonus,
                delta_pence=100, reason="  ", actor="admin")


class TestReopen:
    def test_reconciled_run_blocks_override(
            self, session: Session, run: PayrollRun) -> None:
        payrun.reconcile(session, run, {})
        with pytest.raises(PayrollError, match="re-open"):
            payrun.apply_override(
                session, run, kind=PayrollComponentKind.bonus,
                delta_pence=100, reason="x", actor="admin")

    def test_reopen_restores_mutability(
            self, session: Session, run: PayrollRun) -> None:
        payrun.reconcile(session, run, {})
        payrun.reopen(session, run)
        assert run.status is PayrollRunStatus.forecast
        payrun.apply_override(
            session, run, kind=PayrollComponentKind.bonus,
            delta_pence=500, reason="Late award", actor="admin")
        assert run.components[-1].label == \
            "Manual override — Late award"

    def test_reopen_preserves_actuals(
            self, session: Session, run: PayrollRun) -> None:
        tax_line = next(c for c in run.components
                        if c.kind is PayrollComponentKind.tax)
        payrun.reconcile(session, run,
                         {PayrollComponentKind.tax: -99_999})
        payrun.reopen(session, run)
        assert tax_line.actual_pence == -99_999

    def test_corrected_run_is_immutable(
            self, session: Session, run: PayrollRun) -> None:
        payrun.reconcile(session, run, {})
        payrun.correct(session, run)
        with pytest.raises(PayrollError):
            payrun.reopen(session, run)
        with pytest.raises(PayrollError):
            payrun.apply_override(
                session, run, kind=PayrollComponentKind.bonus,
                delta_pence=100, reason="x", actor="admin")

    def test_open_run_needs_no_reopen(
            self, session: Session, run: PayrollRun) -> None:
        with pytest.raises(PayrollError, match="reconciled"):
            payrun.reopen(session, run)


class TestIsolation:
    def test_override_never_flows_to_future_runs(
            self, session: Session, emp: Employment,
            run: PayrollRun) -> None:
        """AC: overrides are excluded from future forecasts."""
        payrun.apply_override(
            session, run, kind=PayrollComponentKind.bonus,
            delta_pence=10_000, reason="One-off", actor="admin")
        nxt = payrun.generate(
            session, emp, start=date(2025, 5, 1),
            end=date(2025, 5, 31), pay_date=date(2025, 5, 28))
        assert all("override" not in c.label.lower()
                   for c in nxt.components)
        assert list(session.execute(
            select(PayrollOverride).where(
                PayrollOverride.run_id == nxt.id)).scalars()) == []

    def test_later_override_leaves_earlier_run_unchanged(
            self, session: Session, emp: Employment,
            run: PayrollRun) -> None:
        """AC: historical runs unchanged by later overrides."""
        nxt = payrun.generate(
            session, emp, start=date(2025, 5, 1),
            end=date(2025, 5, 31), pay_date=date(2025, 5, 28))
        g0, n0 = run.gross_pence, run.net_pence
        payrun.apply_override(
            session, nxt, kind=PayrollComponentKind.bonus,
            delta_pence=42_000, reason="New run tweak", actor="admin")
        session.flush()
        session.refresh(run)
        assert (run.gross_pence, run.net_pence) == (g0, n0)


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def _employment(self, client: TestClient) -> tuple[dict, int]:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        pid = client.post("/api/v1/profiles", headers=h,
                          json={"display_name": "J"}).json()["id"]
        e = client.post("/api/v1/employments", headers=h, json={
            "profile_id": pid, "employer": "Acme",
            "jurisdiction": "england_wales",
            "ni_category": "A", "contracted_hours": "37.5"})
        assert e.status_code == 201, e.json()
        eid = e.json()["id"]
        client.post(f"/api/v1/employments/{eid}/salary-revisions",
                    headers=h, json={
                        "effective_date": "2025-01-01",
                        "annual_salary": 3_600_000})
        client.post(f"/api/v1/employments/{eid}/tax-code-revisions",
                    headers=h, json={
                        "effective_date": "2025-01-01",
                        "tax_code": "1257L"})
        return h, eid

    def test_override_reopen_flow(self, client: TestClient) -> None:
        h, eid = self._employment(client)
        r = client.post(
            f"/api/v1/employments/{eid}/payroll-runs", headers=h,
            json={"period_start": "2025-04-01",
                  "period_end": "2025-04-30",
                  "pay_date": "2025-04-28"})
        rid = r.json()["id"]
        client.post(
            f"/api/v1/employments/{eid}/payroll-runs/{rid}/reconcile",
            headers=h, json={"actuals": {}})
        # reconciled → override blocked
        blocked = client.post(
            f"/api/v1/employments/{eid}/payroll-runs/{rid}/overrides",
            headers=h, json={"kind": "bonus", "delta": 1000,
                             "reason": "Spot"})
        assert blocked.status_code == 409
        # reopen → override allowed, audited
        ro = client.post(
            f"/api/v1/employments/{eid}/payroll-runs/{rid}/reopen",
            headers=h)
        assert ro.status_code == 200
        ov = client.post(
            f"/api/v1/employments/{eid}/payroll-runs/{rid}/overrides",
            headers=h, json={"kind": "bonus", "delta": 1000,
                             "reason": "Spot award"})
        assert ov.status_code == 201, ov.json()
        detail = client.get(
            f"/api/v1/employments/{eid}/payroll-runs/{rid}",
            headers=h).json()
        assert any(c["label"].startswith("Manual override")
                   for c in detail["components"])
