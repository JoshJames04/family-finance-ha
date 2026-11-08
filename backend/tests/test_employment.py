"""Issue #78 — employment + effective-dated salary/tax-code history."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import payroll as dom
from app.domain.payroll import PayrollError
from app.main import create_app
from app.persistence.models import Employment, Household, UserProfile


@pytest.fixture()
def emp(session: Session, household: Household) -> Employment:
    p = UserProfile(household_id=household.id, display_name="Alice")
    session.add(p)
    session.flush()
    e = Employment(household_id=household.id, profile_id=p.id,
                   employer="Acme", jurisdiction="england_wales",
                   contracted_hours=Decimal("37.5"))
    session.add(e)
    session.flush()
    return e


class TestRevisions:
    def test_effective_boundary(self, session: Session,
                                emp: Employment) -> None:
        dom.add_salary_revision(session, emp, date(2026, 1, 1),
                                3_000_000)
        dom.add_salary_revision(session, emp, date(2026, 6, 1),
                                3_600_000)
        assert dom.salary_at(session, emp.id,
                             date(2026, 5, 31)) == 3_000_000
        assert dom.salary_at(session, emp.id,
                             date(2026, 6, 1)) == 3_600_000

    def test_no_revision_before_first(self, session: Session,
                                      emp: Employment) -> None:
        dom.add_salary_revision(session, emp, date(2026, 3, 1),
                                3_000_000)
        with pytest.raises(PayrollError):
            dom.salary_at(session, emp.id, date(2026, 2, 28))

    def test_overlapping_rejected(self, session: Session,
                                  emp: Employment) -> None:
        """AC: overlapping revisions rejected (same effective date)."""
        dom.add_salary_revision(session, emp, date(2026, 1, 1),
                                3_000_000)
        with pytest.raises(PayrollError):
            dom.add_salary_revision(session, emp, date(2026, 1, 1),
                                    3_100_000)
        dom.add_tax_code_revision(session, emp, date(2026, 1, 1),
                                  "1257L")
        with pytest.raises(PayrollError):
            dom.add_tax_code_revision(session, emp, date(2026, 1, 1),
                                      "BR")

    def test_tax_code_series(self, session: Session,
                             emp: Employment) -> None:
        dom.add_tax_code_revision(session, emp, date(2026, 4, 6),
                                  "1257L")
        dom.add_tax_code_revision(session, emp, date(2026, 9, 1),
                                  "1000L")
        assert dom.tax_code_at(session, emp.id,
                               date(2026, 8, 31)) == "1257L"
        assert dom.tax_code_at(session, emp.id,
                               date(2026, 9, 1)) == "1000L"


class TestDerivations:
    def test_hourly_rate(self) -> None:
        # £36,000/yr at 37.5h/wk → 3600000/(37.5*52) = 1846.15 → 1846p
        assert dom.hourly_rate_pence(3_600_000,
                                     Decimal("37.5")) == 1846

    def test_proration_mid_period_change(
            self, session: Session, emp: Employment) -> None:
        """AC: mid-period salary change prorates correctly.
        April 2026 (30d): £36,000 to Apr 15, £43,800 from Apr 16.
        gross = 3600000*15//365 + 4380000*15//365."""
        dom.add_salary_revision(session, emp, date(2025, 4, 6),
                                3_600_000)
        dom.add_salary_revision(session, emp, date(2026, 4, 16),
                                4_380_000)
        got = dom.gross_for_period(session, emp.id,
                                   date(2026, 4, 1), date(2026, 4, 30))
        expected = (3_600_000 * 15 // 365) + (4_380_000 * 15 // 365)
        assert got == expected

    def test_proration_employment_starts_mid_period(
            self, session: Session, emp: Employment) -> None:
        """Days before the first revision contribute zero."""
        dom.add_salary_revision(session, emp, date(2026, 4, 16),
                                3_650_000)
        got = dom.gross_for_period(session, emp.id,
                                   date(2026, 4, 1), date(2026, 4, 30))
        assert got == 3_650_000 * 15 // 365

    def test_gross_requires_revisions(self, session: Session,
                                    emp: Employment) -> None:
        with pytest.raises(PayrollError):
            dom.gross_for_period(session, emp.id,
                                 date(2026, 4, 1), date(2026, 4, 30))


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_full_flow(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        pid = client.post("/api/v1/profiles",
                          json={"display_name": "Alice"},
                          headers=h).json()["id"]
        eid = client.post("/api/v1/employments", json={
            "profile_id": pid, "employer": "Acme",
            "contracted_hours": 37.5}, headers=h).json()["id"]
        r = client.post(f"/api/v1/employments/{eid}/salary-revisions",
                        json={"effective_date": "2026-04-06",
                              "annual_salary": 3600000}, headers=h)
        assert r.status_code == 201
        # duplicate effective date → 409
        assert client.post(
            f"/api/v1/employments/{eid}/salary-revisions",
            json={"effective_date": "2026-04-06",
                  "annual_salary": 1}, headers=h).status_code == 409
        s = client.get(f"/api/v1/employments/{eid}/salary"
                       "?on=2026-05-01", headers=h).json()
        assert s["annual_salary_pence"] == 3600000
        assert s["hourly_rate_pence"] == 1846
        g = client.get(f"/api/v1/employments/{eid}/gross"
                       "?start=2026-04-01&end=2026-04-30",
                       headers=h).json()
        # revision effective Apr 6 → Apr 1–5 contribute zero
        assert g["gross_pence"] == 3_600_000 * 25 // 365
        # scoping: another household can't see it
        hid2 = client.post("/api/v1/households",
                           json={"name": "Other"}).json()["id"]
        assert client.get(f"/api/v1/employments/{eid}",
                          headers={"X-Household-Id": str(hid2)}
                          ).status_code == 404

    def test_member_scoped_list(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        p1 = client.post("/api/v1/profiles",
                         json={"display_name": "A"},
                         headers=h).json()["id"]
        p2 = client.post("/api/v1/profiles",
                         json={"display_name": "B"},
                         headers=h).json()["id"]
        for pid in (p1, p2):
            client.post("/api/v1/employments", json={
                "profile_id": pid, "employer": "E",
                "contracted_hours": 40}, headers=h)
        all_rows = client.get("/api/v1/employments",
                              headers=h).json()["items"]
        assert len(all_rows) == 2
        mine = client.get("/api/v1/employments",
                          headers={**h, "X-Profile-Id": str(p1)}
                          ).json()["items"]
        assert len(mine) == 1 and mine[0]["profile_id"] == p1

    def test_revision_patch_and_delete(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        pid = client.post("/api/v1/profiles",
                          json={"display_name": "Alice"},
                          headers=h).json()["id"]
        eid = client.post("/api/v1/employments", json={
            "profile_id": pid, "employer": "Acme",
            "contracted_hours": 37.5}, headers=h).json()["id"]
        r1 = client.post(f"/api/v1/employments/{eid}/salary-revisions",
                         json={"effective_date": "2026-04-06",
                               "annual_salary": 3600000},
                         headers=h).json()
        r2 = client.post(f"/api/v1/employments/{eid}/salary-revisions",
                         json={"effective_date": "2026-10-01",
                               "annual_salary": 4000000},
                         headers=h).json()

        # correct a figure and note in place
        p = client.patch(
            f"/api/v1/employments/{eid}/salary-revisions/{r2['id']}",
            json={"annual_salary": 4100000, "notes": "corrected"},
            headers=h)
        assert p.status_code == 200
        assert p.json()["annual_salary_pence"] == 4100000
        assert p.json()["notes"] == "corrected"

        # moving the date onto an existing revision date → 409
        assert client.patch(
            f"/api/v1/employments/{eid}/salary-revisions/{r2['id']}",
            json={"effective_date": "2026-04-06"},
            headers=h).status_code == 409
        # moving to a free date is fine
        assert client.patch(
            f"/api/v1/employments/{eid}/salary-revisions/{r2['id']}",
            json={"effective_date": "2026-10-05"},
            headers=h).status_code == 200

        # cross-household revision → 404
        hid2 = client.post("/api/v1/households",
                           json={"name": "Other"}).json()["id"]
        assert client.patch(
            f"/api/v1/employments/{eid}/salary-revisions/{r2['id']}",
            json={"annual_salary": 1},
            headers={"X-Household-Id": str(hid2)}).status_code == 404

        # delete one of two → 204; deleting the last one → 409
        assert client.delete(
            f"/api/v1/employments/{eid}/salary-revisions/{r2['id']}",
            headers=h).status_code == 204
        assert client.delete(
            f"/api/v1/employments/{eid}/salary-revisions/{r1['id']}",
            headers=h).status_code == 409
