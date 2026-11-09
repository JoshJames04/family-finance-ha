"""Issue #82 — payroll runs: forecast → payslip → reconciled with
component-level variance; immutable except via correction."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import overtime as ot
from app.domain import payroll as dom
from app.domain import payrun
from app.domain.payroll import PayrollError
from app.main import create_app
from app.persistence.models import (
    Employment,
    Household,
    PayrollComponentKind,
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


class TestGenerate:
    def test_component_assembly(self, session: Session,
                                emp: Employment) -> None:
        """Run = prorated salary + overtime − deductions, all broken
        out as components with the rule-set version pinned."""
        run = payrun.generate(session, emp, start=APR_S, end=APR_E,
                              pay_date=PAY)
        assert run.status is PayrollRunStatus.forecast
        assert run.tax_year == "2025-26"
        assert run.rule_set_version == 1
        base = 3_600_000 * 30 // 365
        assert run.gross_pence == base
        kinds = {c.kind: c.forecast_pence for c in run.components}
        assert kinds[PayrollComponentKind.base_salary] == base
        # taxable £2,958.90, PA £1,047.50 → £1,911.40 @20% = £382.28
        assert kinds[PayrollComponentKind.tax] == -38_228
        # NI (£2,958.90 − £1,047.50) @8% = £152.91
        assert kinds[PayrollComponentKind.ni] == -15_291
        assert run.net_pence == base - 38_228 - 15_291

    def test_overtime_and_adjustments(self, session: Session,
                                    emp: Employment) -> None:
        o = ot.create_entry(session, emp, work_date=date(2025, 4, 12),
                            hours=Decimal("4"))
        ot.submit(o)
        ot.approve(o)
        run = payrun.generate(session, emp, start=APR_S, end=APR_E,
                              pay_date=PAY, bonus_pence=50_000,
                              unpaid_leave_pence=10_000)
        kinds = {c.kind: c.forecast_pence for c in run.components}
        assert kinds[PayrollComponentKind.overtime] == o.pay_pence
        assert kinds[PayrollComponentKind.bonus] == 50_000
        assert kinds[PayrollComponentKind.unpaid_leave] == -10_000
        base = 3_600_000 * 30 // 365
        assert run.gross_pence == base + o.pay_pence + 50_000

    def test_allowance_joins_earnings_and_taxes(
            self, session: Session, emp: Employment) -> None:
        """#399 — a fixed allowance (fire-warden/first-aider style)
        lands as its own component, joins gross, and is taxed +
        NI'd through compute_period like any other earning."""
        plain = payrun.generate(session, emp, start=APR_S, end=APR_E,
                                pay_date=PAY)
        run = payrun.generate(session, emp, start=APR_S, end=APR_E,
                              pay_date=PAY, allowance_pence=15_000)
        kinds = {c.kind: c.forecast_pence for c in run.components}
        assert kinds[PayrollComponentKind.allowance] == 15_000
        assert run.gross_pence == plain.gross_pence + 15_000
        # allowance is taxable + NIable: both deductions grow
        assert abs(kinds[PayrollComponentKind.tax]) \
            > abs(plain.gross_pence - plain.net_pence) - abs(
                kinds[PayrollComponentKind.ni])
        assert run.net_pence < plain.net_pence + 15_000

    def test_period_validation(self, session: Session,
                               emp: Employment) -> None:
        with pytest.raises(PayrollError):
            payrun.generate(session, emp, start=APR_E, end=APR_S,
                            pay_date=PAY)


class TestReconcile:
    def test_variance_breakdown(self, session: Session,
                                emp: Employment) -> None:
        """AC: payslip differing from forecast shows exactly which
        component diverged."""
        run = payrun.generate(session, emp, start=APR_S, end=APR_E,
                              pay_date=PAY)
        variances = payrun.reconcile(
            session, run, {PayrollComponentKind.tax: -38_500})
        assert len(variances) == 1
        v = variances[0]
        assert v.kind is PayrollComponentKind.tax
        assert v.forecast_pence == -38_228
        assert v.actual_pence == -38_500
        assert v.delta_pence == -272
        assert run.status is PayrollRunStatus.reconciled

    def test_unlisted_actual_becomes_component(
            self, session: Session, emp: Employment) -> None:
        run = payrun.generate(session, emp, start=APR_S, end=APR_E,
                              pay_date=PAY)
        variances = payrun.reconcile(
            session, run, {PayrollComponentKind.bonus: 75_000})
        assert any(v.kind is PayrollComponentKind.bonus
                   and v.delta_pence == 75_000 for v in variances)
        assert component_variance_has(run,
                                      PayrollComponentKind.bonus)

    def test_clean_payslip_zero_variance(self, session: Session,
                                         emp: Employment) -> None:
        run = payrun.generate(session, emp, start=APR_S, end=APR_E,
                              pay_date=PAY)
        assert payrun.reconcile(session, run, {}) == []
        assert run.status is PayrollRunStatus.reconciled


def component_variance_has(run: PayrollRun,
                           kind: PayrollComponentKind) -> bool:
    return any(v.kind is kind
               for v in payrun.component_variance(run))


def _pension_emp(session: Session, household: Household, mode: str,
                 bps: int = 0, fixed: int = 0) -> Employment:
    p = UserProfile(household_id=household.id, display_name="P")
    session.add(p)
    session.flush()
    e = Employment(household_id=household.id, profile_id=p.id,
                   employer="Acme", jurisdiction="england_wales",
                   contracted_hours=Decimal("37.5"),
                   pension_mode=mode, pension_employee_bps=bps,
                   pension_fixed_pence=fixed)
    session.add(e)
    session.flush()
    dom.add_salary_revision(session, e, date(2025, 1, 1), 3_600_000)
    dom.add_tax_code_revision(session, e, date(2025, 1, 1), "1257L")
    return e


class TestPensionAndLeave:
    """Issue #83 — each component an explicit line; ordering vs the
    tax engine verified per mode."""

    def _kinds(self, run):
        return {c.kind: c.forecast_pence for c in run.components}

    def test_net_pay_percent(self, session: Session,
                             household: Household) -> None:
        e = _pension_emp(session, household, "net_pay", bps=500)
        run = payrun.generate(session, e, start=APR_S, end=APR_E,
                              pay_date=PAY)
        k = self._kinds(run)
        # 5% of £2,958.90 = £147.94; taxable £2,810.96 → tax £352.69
        assert k[PayrollComponentKind.pension] == -14_794
        assert k[PayrollComponentKind.tax] == -35_269
        assert k[PayrollComponentKind.ni] == -15_291  # NI on full gross

    def test_sacrifice_reduces_ni(self, session: Session,
                                  household: Household) -> None:
        e = _pension_emp(session, household, "salary_sacrifice",
                         bps=500)
        run = payrun.generate(session, e, start=APR_S, end=APR_E,
                              pay_date=PAY)
        k = self._kinds(run)
        assert k[PayrollComponentKind.pension] == -14_794
        assert k[PayrollComponentKind.tax] == -35_269
        assert k[PayrollComponentKind.ni] == -14_107  # NI reduced too

    def test_ras_tax_unchanged_net_reduced(
            self, session: Session, household: Household) -> None:
        e = _pension_emp(session, household, "relief_at_source",
                         bps=500)
        run = payrun.generate(session, e, start=APR_S, end=APR_E,
                              pay_date=PAY)
        k = self._kinds(run)
        assert k[PayrollComponentKind.tax] == -38_228   # same as none
        assert k[PayrollComponentKind.pension] == -14_794
        assert run.net_pence == 295_890 - 38_228 - 15_291 - 14_794

    def test_fixed_beats_percent(self, session: Session,
                                 household: Household) -> None:
        e = _pension_emp(session, household, "net_pay", bps=500,
                         fixed=20_000)
        run = payrun.generate(session, e, start=APR_S, end=APR_E,
                              pay_date=PAY)
        assert self._kinds(run)[PayrollComponentKind.pension] == -20_000

    def test_unpaid_leave_day_proration(
            self, session: Session, household: Household) -> None:
        """AC: unpaid day reduces gross per contracted-hours
        proration — £36k / (52×5) = £138.46/day, then pension % and
        tax apply on the reduced base."""
        e = _pension_emp(session, household, "net_pay", bps=500)
        run = payrun.generate(session, e, start=APR_S, end=APR_E,
                              pay_date=PAY,
                              unpaid_leave_days=Decimal("1"))
        k = self._kinds(run)
        assert k[PayrollComponentKind.unpaid_leave] == -13_846
        # pensionable = £2,958.90 − £138.46 = £2,820.44 → 5% = £141.02
        assert k[PayrollComponentKind.pension] == -14_102
        # taxable 282044−14102 = 267942 → PA 104750 → tax £326.38
        assert k[PayrollComponentKind.tax] == -32_638
        assert run.gross_pence == 295_890  # earnings line unaffected

    def test_zero_config_no_pension_line(
            self, session: Session, emp: Employment) -> None:
        run = payrun.generate(session, emp, start=APR_S, end=APR_E,
                              pay_date=PAY)
        assert PayrollComponentKind.pension not in self._kinds(run)


class TestImmutability:
    def test_reconciled_cannot_re_reconcile(
            self, session: Session, emp: Employment) -> None:
        run = payrun.generate(session, emp, start=APR_S, end=APR_E,
                              pay_date=PAY)
        payrun.reconcile(session, run, {})
        with pytest.raises(PayrollError):
            payrun.reconcile(session, run,
                             {PayrollComponentKind.tax: -1})

    def test_correct_supersedes(self, session: Session,
                                emp: Employment) -> None:
        """AC: reconciled runs immutable except via correction — the
        correction run carries actuals as its new forecast."""
        run = payrun.generate(session, emp, start=APR_S, end=APR_E,
                              pay_date=PAY)
        payrun.reconcile(session, run,
                         {PayrollComponentKind.tax: -38_500})
        new = payrun.correct(session, run)
        assert run.status is PayrollRunStatus.corrected
        assert new.status is PayrollRunStatus.forecast
        assert new.corrects_id == run.id
        kinds = {c.kind: c.forecast_pence for c in new.components}
        assert kinds[PayrollComponentKind.tax] == -38_500  # was actual

    def test_correct_requires_reconciled(
            self, session: Session, emp: Employment) -> None:
        run = payrun.generate(session, emp, start=APR_S, end=APR_E,
                              pay_date=PAY)
        with pytest.raises(PayrollError):
            payrun.correct(session, run)


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def _setup(self, client: TestClient
               ) -> tuple[dict[str, str], int]:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        pid = client.post("/api/v1/profiles",
                          json={"display_name": "J"},
                          headers=h).json()["id"]
        eid = client.post("/api/v1/employments", json={
            "profile_id": pid, "employer": "Acme",
            "contracted_hours": 37.5}, headers=h).json()["id"]
        client.post(f"/api/v1/employments/{eid}/salary-revisions",
                    json={"effective_date": "2025-01-01",
                          "annual_salary": 3600000}, headers=h)
        client.post(f"/api/v1/employments/{eid}/tax-code-revisions",
                    json={"effective_date": "2025-01-01",
                          "tax_code": "1257L"}, headers=h)
        return h, eid

    def test_full_flow(self, client: TestClient) -> None:
        h, eid = self._setup(client)
        r = client.post(f"/api/v1/employments/{eid}/payroll-runs",
                        json={"period_start": "2025-04-01",
                              "period_end": "2025-04-30",
                              "pay_date": "2025-04-28"}, headers=h)
        assert r.status_code == 201
        run = r.json()
        assert run["status"] == "forecast"
        assert run["tax_year"] == "2025-26"
        comps = {c["kind"]: c for c in run["components"]}
        assert comps["base_salary"]["forecast_pence"] == 295_890
        assert comps["tax"]["forecast_pence"] == -38_228
        rid = run["id"]
        # reconcile with a diverging payslip
        r = client.post(
            f"/api/v1/employments/{eid}/payroll-runs/{rid}/reconcile",
            json={"actuals": {"tax": -38500}}, headers=h)
        assert r.status_code == 200
        tax = {c["kind"]: c for c in r.json()["components"]}["tax"]
        assert tax["actual_pence"] == -38_500
        assert tax["variance_pence"] == -272
        # reconciled → immutable: re-reconcile is 409
        assert client.post(
            f"/api/v1/employments/{eid}/payroll-runs/{rid}/reconcile",
            json={"actuals": {}}, headers=h).status_code == 409
        # correction supersedes
        r = client.post(
            f"/api/v1/employments/{eid}/payroll-runs/{rid}/correct",
            headers=h)
        assert r.status_code == 201
        new = r.json()
        assert new["corrects_id"] == rid
        assert new["status"] == "forecast"

    def test_scoping(self, client: TestClient) -> None:
        h, eid = self._setup(client)
        client.post(f"/api/v1/employments/{eid}/payroll-runs",
                    json={"period_start": "2025-04-01",
                          "period_end": "2025-04-30",
                          "pay_date": "2025-04-28"}, headers=h)
        hid2 = client.post("/api/v1/households",
                           json={"name": "X"}).json()["id"]
        assert client.get(
            f"/api/v1/employments/{eid}/payroll-runs",
            headers={"X-Household-Id": str(hid2)}).status_code == 404
