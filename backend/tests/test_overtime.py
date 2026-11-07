"""Issue #79 — overtime entries: day-type multipliers, snapshotted
rates, draft→submitted→approved flow, approved-only payroll feed."""

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
from app.domain.payroll import PayrollError
from app.main import create_app
from app.persistence.models import (
    DayType,
    Employment,
    Household,
    OvertimeEntry,
    OvertimeStatus,
    UserProfile,
)

# 2026-04-03 Good Friday, 04 Sat, 05 Sun, 06 Easter Monday, 07 Tue.
FRI = date(2026, 4, 3)
SAT = date(2026, 4, 4)
SUN = date(2026, 4, 5)
BH_MON = date(2026, 4, 6)
TUE = date(2026, 4, 7)


@pytest.fixture()
def emp(session: Session, household: Household) -> Employment:
    p = UserProfile(household_id=household.id, display_name="J")
    session.add(p)
    session.flush()
    e = Employment(household_id=household.id, profile_id=p.id,
                   employer="Acme", jurisdiction="england_wales",
                   contracted_hours=Decimal("37.5"),
                   overtime_multipliers=(
                       '{"weekday": 15000, "saturday": 15000,'
                       ' "sunday": 20000, "bank_holiday": 20000}'))
    session.add(e)
    session.flush()
    dom.add_salary_revision(session, e, date(2026, 1, 1), 3_600_000)
    return e


class TestDayTypes:
    def test_classification(self) -> None:
        assert ot.day_type_for(SAT) is DayType.saturday
        assert ot.day_type_for(SUN) is DayType.sunday
        assert ot.day_type_for(BH_MON) is DayType.bank_holiday
        assert ot.day_type_for(FRI) is DayType.bank_holiday
        assert ot.day_type_for(TUE) is DayType.weekday

    def test_defaults_from_employment_config(
            self, session: Session, emp: Employment) -> None:
        """AC: Sunday entry defaults to the configured Sunday
        multiplier (2×), not a universal constant."""
        o = ot.create_entry(session, emp, work_date=SUN,
                            hours=Decimal("4"))
        assert o.day_type is DayType.sunday
        assert o.multiplier_bps == 20_000
        o2 = ot.create_entry(session, emp, work_date=TUE,
                             hours=Decimal("2"))
        assert o2.multiplier_bps == 15_000

    def test_custom_override(self, session: Session,
                             emp: Employment) -> None:
        """AC: default overridable — explicit multiplier wins."""
        o = ot.create_entry(session, emp, work_date=SUN,
                            hours=Decimal("4"), multiplier_bps=12_500)
        assert o.multiplier_bps == 12_500

    def test_unconfigured_falls_back(
            self, session: Session, household: Household) -> None:
        p = UserProfile(household_id=household.id, display_name="X")
        session.add(p)
        session.flush()
        e = Employment(household_id=household.id, profile_id=p.id,
                       employer="B", jurisdiction="england_wales",
                       contracted_hours=Decimal("40"))
        session.add(e)
        session.flush()
        dom.add_salary_revision(session, e, date(2026, 1, 1),
                                3_600_000)
        o = ot.create_entry(session, e, work_date=SUN,
                            hours=Decimal("1"))
        assert o.multiplier_bps == ot.FALLBACK_MULTIPLIERS[
            DayType.sunday]


class TestRateDerivation:
    def test_snapshot_at_work_date(self, session: Session,
                                   emp: Employment) -> None:
        """Rate = derived hourly at the ENTRY date — a later raise
        doesn't re-rate an existing entry."""
        o = ot.create_entry(session, emp, work_date=TUE,
                            hours=Decimal("2"))
        assert o.hourly_rate_pence == 1846  # £36k @ 37.5h
        dom.add_salary_revision(session, emp, date(2026, 5, 1),
                                4_380_000)
        o2 = ot.create_entry(session, emp, work_date=date(2026, 5, 5),
                             hours=Decimal("2"))
        assert o2.hourly_rate_pence != o.hourly_rate_pence
        # earlier entry unchanged — snapshot is permanent
        assert o.hourly_rate_pence == 1846

    def test_pay_math(self, session: Session, emp: Employment) -> None:
        o = ot.create_entry(session, emp, work_date=TUE,
                            hours=Decimal("2.5"))
        # 2.5h × £18.46 × 1.5 = £69.225 → floor £69.22
        assert o.pay_pence == 6_922


def _status(o: OvertimeEntry) -> OvertimeStatus:
    """Read status without mypy's literal narrowing on `is` asserts —
    the domain mutates it inside transitions."""
    return o.status


class TestStatusFlow:
    def test_happy_path(self, session: Session,
                        emp: Employment) -> None:
        o = ot.create_entry(session, emp, work_date=TUE,
                            hours=Decimal("1"))
        assert _status(o) is OvertimeStatus.draft
        ot.submit(o)
        assert _status(o) is OvertimeStatus.submitted
        ot.approve(o)
        assert _status(o) is OvertimeStatus.approved

    def test_invalid_transitions(self, session: Session,
                                 emp: Employment) -> None:
        o = ot.create_entry(session, emp, work_date=TUE,
                            hours=Decimal("1"))
        with pytest.raises(PayrollError):
            ot.approve(o)          # draft can't skip to approved
        with pytest.raises(PayrollError):
            ot.reject(o)           # draft can't be rejected
        ot.submit(o)
        ot.approve(o)
        with pytest.raises(PayrollError):
            ot.submit(o)           # approved is terminal

    def test_rejected_rework(self, session: Session,
                             emp: Employment) -> None:
        o = ot.create_entry(session, emp, work_date=TUE,
                            hours=Decimal("1"))
        ot.submit(o)
        ot.reject(o)
        assert _status(o) is OvertimeStatus.rejected
        draft: OvertimeStatus = OvertimeStatus.draft
        o.status = draft                  # member edits + resubmits
        ot.submit(o)
        ot.approve(o)
        assert _status(o) is OvertimeStatus.approved


class TestApprovedOnly:
    def test_approved_total(self, session: Session,
                            emp: Employment) -> None:
        """AC: only approved entries feed payroll runs."""
        for i, d in enumerate((TUE, SAT, SUN)):
            o = ot.create_entry(session, emp, work_date=d,
                                hours=Decimal(str(i + 1)))
            if i < 2:
                ot.submit(o)
                ot.approve(o)      # 3rd stays draft
        total = ot.approved_total_pence(
            session, emp.id, date(2026, 4, 1), date(2026, 4, 30))
        e1 = session.get(OvertimeEntry, 1)
        e2 = session.get(OvertimeEntry, 2)
        assert e1 is not None and e2 is not None
        expected = ot.entry_pay_pence(e1) + ot.entry_pay_pence(e2)
        assert total == expected
        assert total > 0


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
            "contracted_hours": 37.5,
            "overtime_multipliers": {"sunday": 20000}},
            headers=h).json()["id"]
        client.post(f"/api/v1/employments/{eid}/salary-revisions",
                    json={"effective_date": "2026-01-01",
                          "annual_salary": 3600000}, headers=h)
        return h, eid

    def test_create_and_transition(self, client: TestClient) -> None:
        h, eid = self._setup(client)
        r = client.post(
            f"/api/v1/employments/{eid}/overtime-entries",
            json={"work_date": "2026-04-05", "hours": 4}, headers=h)
        assert r.status_code == 201
        body = r.json()
        assert body["day_type"] == "sunday"
        assert body["multiplier_bps"] == 20000
        assert body["status"] == "draft"
        assert body["hourly_rate_pence"] == 1846
        assert body["pay_pence"] == 4 * 1846 * 2  # ×2.0
        oid = body["id"]
        # invalid jump: draft → approve = 409
        assert client.post(
            f"/api/v1/employments/{eid}/overtime-entries/{oid}/approve",
            headers=h).status_code == 409
        for act in ("submit", "approve"):
            r = client.post(
                f"/api/v1/employments/{eid}/overtime-entries/{oid}/{act}",
                headers=h)
            assert r.status_code == 200
        assert r.json()["status"] == "approved"
        listed = client.get(
            f"/api/v1/employments/{eid}/overtime-entries"
            "?status=approved", headers=h).json()
        assert len(listed) == 1

    def test_scoping(self, client: TestClient) -> None:
        h, eid = self._setup(client)
        client.post(f"/api/v1/employments/{eid}/overtime-entries",
                    json={"work_date": "2026-04-05", "hours": 1},
                    headers=h)
        hid2 = client.post("/api/v1/households",
                           json={"name": "X"}).json()["id"]
        assert client.get(
            f"/api/v1/employments/{eid}/overtime-entries",
            headers={"X-Household-Id": str(hid2)}).status_code == 404
