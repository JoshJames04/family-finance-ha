"""Weekly overtime snapshot, transaction, privacy and payroll regressions."""
from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, timedelta
from pathlib import Path
from threading import Barrier, Event
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1 import employments
from app.core.settings import Settings
from app.domain import overtime as ot
from app.main import create_app
from app.persistence.models import Account, AccountType, AuditEvent, Employment

MONDAY = "2026-12-28"  # ISO 2026-W53, straddles the year and month.


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
        yield c


@pytest.fixture()
def scope(client: TestClient) -> tuple[dict[str, str], int]:
    hid = client.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
    h = {"X-Household-Id": str(hid)}
    pid = client.post("/api/v1/profiles", headers=h,
                      json={"display_name": "Owner", "role": "admin"}).json()["id"]
    h["X-Profile-Id"] = str(pid)
    eid = client.post("/api/v1/employments", headers=h, json={
        "profile_id": pid, "employer": "Synthetic", "contracted_hours": 37.5,
    }).json()["id"]
    assert client.post(f"/api/v1/employments/{eid}/salary-revisions", headers=h,
                       json={"effective_date": "2020-01-01", "annual_salary": 3600000}
                       ).status_code == 201
    assert client.post(f"/api/v1/employments/{eid}/tax-code-revisions", headers=h,
                       json={"effective_date": "2020-01-01", "tax_code": "1257L"}
                       ).status_code == 201
    return h, eid


def create(client: TestClient, scope: tuple[dict[str, str], int],
           day: str = MONDAY, status: str = "draft") -> dict[str, Any]:
    h, eid = scope
    response = client.post(f"/api/v1/employments/{eid}/overtime-entries", headers=h,
                           json={"work_date": day, "hours": 2})
    assert response.status_code == 201, response.text
    row = response.json()
    for action in {"draft": (), "submitted": ("submit",),
                   "approved": ("submit", "approve"),
                   "rejected": ("submit", "reject")}[status]:
        response = client.post(
            f"/api/v1/employments/{eid}/overtime-entries/{row['id']}/{action}", headers=h)
        assert response.status_code == 200
        row = response.json()
    return row


def snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"entries": [{"id": row["id"], "revision": row.get("revision", "missing")}
                        for row in rows]}


def weekly(client: TestClient, scope: tuple[dict[str, str], int],
           rows: list[dict[str, Any]], action: str = "submit", day: str = MONDAY):
    h, eid = scope
    return client.post(f"/api/v1/employments/{eid}/overtime-weeks/{day}/{action}",
                       headers=h, json=snapshot(rows))


def rows(client: TestClient, scope: tuple[dict[str, str], int]) -> list[dict[str, Any]]:
    h, eid = scope
    return client.get(f"/api/v1/employments/{eid}/overtime-entries", headers=h).json()


def audits(client: TestClient) -> list[tuple[int, str, dict[str, Any]]]:
    with Session(client.app.state.engine) as session:
        return [(a.entity_id, a.action, json.loads(a.payload or "{}"))
                for a in session.scalars(select(AuditEvent).where(
                    AuditEvent.entity_type == "overtime_entry"))]


@pytest.mark.parametrize("count", [1, 7])
@pytest.mark.parametrize("action,source,target", [
    ("submit", "draft", "submitted"), ("approve", "submitted", "approved"),
    ("reject", "submitted", "rejected"),
])
def test_week_changes_only_complete_eligible_set(client, scope, count, action, source, target):
    eligible = [create(client, scope, (date(2026, 12, 28) + timedelta(days=i)).isoformat(),
                       source) for i in range(count)]
    eligible.append(create(client, scope, MONDAY, source))  # multiple same-day entries
    other = [create(client, scope, MONDAY, status)
             for status in ("draft", "submitted", "approved", "rejected") if status != source]
    outside = [create(client, scope, "2026-12-27", source),
               create(client, scope, "2027-01-04", source)]
    before = {r["id"]: r for r in rows(client, scope)}
    audit_count = len(audits(client))
    response = weekly(client, scope, eligible, action)
    assert response.status_code == 200, response.text
    assert response.json() == {"week_start": MONDAY, "week_end": "2027-01-03",
                               "action": action, "updated_count": count + 1}
    after = {r["id"]: r for r in rows(client, scope)}
    for row in eligible:
        assert after[row["id"]]["status"] == target
        for key in ("hours", "work_date", "hourly_rate_pence", "multiplier_bps", "pay_pence"):
            assert after[row["id"]][key] == before[row["id"]][key]
    for row in other + outside:
        assert after[row["id"]] == before[row["id"]]
    emitted = audits(client)[audit_count:]
    assert len(emitted) == count + 1
    assert {a[0] for a in emitted} == {r["id"] for r in eligible}
    assert all(a[1] == action and a[2]["week_start"] == MONDAY
               and a[2]["week_end"] == "2027-01-03" for a in emitted)
    assert weekly(client, scope, eligible, action).status_code == 409
    assert len(audits(client)) == audit_count + count + 1


@pytest.mark.parametrize("change", ["hours", "notes", "date", "delete", "new", "status"])
def test_stale_snapshot_rolls_back_every_entry_and_audit(client, scope, change):
    initial = [create(client, scope), create(client, scope, "2027-01-03")]
    h, eid = scope
    path = f"/api/v1/employments/{eid}/overtime-entries/{initial[1]['id']}"
    if change in {"hours", "notes", "date"}:
        patch = {"hours": 3} if change == "hours" else (
            {"notes": "edited draft"} if change == "notes" else {"work_date": "2027-01-04"})
        assert client.patch(path, headers=h, json=patch).status_code == 200
    elif change == "delete":
        assert client.delete(path, headers=h).status_code == 204
    elif change == "new":
        create(client, scope)
    else:
        assert client.post(path + "/submit", headers=h).status_code == 200
    before, before_audits = rows(client, scope), audits(client)
    assert weekly(client, scope, initial).status_code == 409
    assert rows(client, scope) == before
    assert audits(client) == before_audits


def test_revision_stable_across_reads_and_changes_on_draft_edit(client, scope):
    row = create(client, scope)
    assert isinstance(row.get("revision"), str) and len(row["revision"]) >= 32
    assert rows(client, scope)[0]["revision"] == row["revision"]
    h, eid = scope
    edited = client.patch(f"/api/v1/employments/{eid}/overtime-entries/{row['id']}",
                          headers=h, json={"notes": "changed in same second"}).json()
    assert edited["status"] == "draft"
    assert edited["revision"] != row["revision"]
    assert rows(client, scope)[0]["revision"] == edited["revision"]


@pytest.mark.parametrize("day,action,entries", [
    ("2026-12-29", "submit", [{"id": 1, "revision": "x"}]),
    ("9999-12-27", "submit", [{"id": 1, "revision": "x"}]),
    (MONDAY, "rework", [{"id": 1, "revision": "x"}]),
    (MONDAY, "submit", []),
    (MONDAY, "submit", [{"id": 0, "revision": "x"}]),
    (MONDAY, "submit", [{"id": -1, "revision": "x"}]),
    (MONDAY, "submit", [{"id": True, "revision": "x"}]),
    (MONDAY, "submit", [{"id": 1, "revision": ""}]),
    (MONDAY, "submit", [{"id": 1, "revision": "x"}, {"id": 1, "revision": "x"}]),
])
def test_invalid_input_is_422_without_writes(client, scope, day, action, entries):
    create(client, scope)
    before, before_audits = rows(client, scope), audits(client)
    h, eid = scope
    response = client.post(f"/api/v1/employments/{eid}/overtime-weeks/{day}/{action}",
                           headers=h, json={"entries": entries})
    assert response.status_code == 422, response.text
    assert rows(client, scope) == before
    assert audits(client) == before_audits


def test_audit_failure_rolls_back_all_week_transitions(client, scope, monkeypatch):
    initial = [create(client, scope), create(client, scope)]
    before_rows = rows(client, scope)
    before = audits(client)
    real_emit = employments.emit
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic audit failure")
        return real_emit(*args, **kwargs)

    monkeypatch.setattr(employments, "emit", fail_second)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        weekly(client, scope, initial)
    assert rows(client, scope) == before_rows
    assert audits(client) == before


@pytest.mark.parametrize("role,own,expected", [
    ("member", True, 200), ("member", False, 404), ("viewer", True, 404),
])
def test_week_actions_use_employment_profile_boundary(client, scope, role, own, expected):
    h, eid = scope
    pid = client.post("/api/v1/profiles", headers=h,
                      json={"display_name": "Other", "role": role}).json()["id"]
    if own:
        # Ownership is assigned at creation; the employment patch intentionally cannot move it.
        with Session(client.app.state.engine) as session:
            employment = session.get(Employment, eid)
            employment.profile_id = pid
            session.commit()
    initial = [create(client, scope)]
    actor = ({**h, "X-Profile-Id": str(pid)}, eid)
    assert weekly(client, actor, initial).status_code == expected


def test_other_employment_ids_conflict_and_other_household_is_hidden(client, scope):
    h, eid = scope
    initial = [create(client, scope)]
    before = rows(client, scope)
    other_id = client.post("/api/v1/employments", headers=h, json={
        "profile_id": int(h["X-Profile-Id"]), "employer": "Other", "contracted_hours": 37.5,
    }).json()["id"]
    assert weekly(client, (h, other_id), initial).status_code == 409
    hid = client.post("/api/v1/households", json={"name": "Other"}).json()["id"]
    assert weekly(client, ({"X-Household-Id": str(hid)}, eid), initial).status_code == 404
    assert rows(client, scope) == before


def test_service_cannot_submit_weeks(client, scope):
    initial = [create(client, scope)]
    before_audits = audits(client)
    with Session(client.app.state.engine) as session:
        account = Account(household_id=int(scope[0]["X-Household-Id"]), name="Shared",
                          type=AccountType.joint, opening_date=date(2020, 1, 1))
        session.add(account)
        session.flush()
        session.get(Employment, scope[1]).paid_into_account_id = account.id
        session.commit()

    async def service(request: Request, call_next):
        request.state.service_authenticated = True
        return await call_next(request)

    client.app.middleware_stack = BaseHTTPMiddleware(client.app.middleware_stack, dispatch=service)
    assert weekly(client, scope, initial).status_code == 403
    assert audits(client) == before_audits
    # The service retains its existing ability to record a draft for a shared destination.
    assert create(client, scope)["status"] == "draft"


def test_weekly_approval_preserves_saved_payroll_and_period_cutoff(client, scope):
    h, eid = scope
    initial = [create(client, scope, "2026-12-31"), create(client, scope, "2027-01-01")]
    run_body = {"period_start": "2026-12-01", "period_end": "2026-12-31",
                "pay_date": "2026-12-31"}
    saved = client.post(f"/api/v1/employments/{eid}/payroll-runs", headers=h,
                        json=run_body)
    assert saved.status_code == 201, saved.text
    saved_body = saved.json()
    assert weekly(client, scope, initial).status_code == 200
    submitted = rows(client, scope)
    with Session(client.app.state.engine) as session:
        assert ot.approved_total_pence(session, eid, date(2026, 12, 1), date(2026, 12, 31)) == 0
    assert weekly(client, scope, submitted, "approve").status_code == 200
    with Session(client.app.state.engine) as session:
        assert ot.approved_total_pence(
            session, eid, date(2026, 12, 1), date(2026, 12, 31)) == initial[0]["pay_pence"]
        assert ot.approved_total_pence(
            session, eid, date(2027, 1, 1), date(2027, 1, 31)) == initial[1]["pay_pence"]
    assert client.get(f"/api/v1/employments/{eid}/payroll-runs/{saved_body['id']}",
                      headers=h).json() == saved_body


def test_old_draft_snapshot_cannot_replay_after_rejection_and_rework(client, scope):
    initial = [create(client, scope)]
    assert weekly(client, scope, initial).status_code == 200
    assert weekly(client, scope, rows(client, scope), "reject").status_code == 200
    h, eid = scope
    assert client.post(
        f"/api/v1/employments/{eid}/overtime-entries/{initial[0]['id']}/rework",
        headers=h).status_code == 200
    assert weekly(client, scope, initial).status_code == 409


def test_concurrent_replay_has_one_effect_and_no_duplicate_audits(client, scope):
    initial = [create(client, scope), create(client, scope)]
    before = len(audits(client))
    barrier = Barrier(2)

    def request():
        barrier.wait(timeout=5)
        return weekly(client, scope, initial).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request(), range(2)))
    assert sorted(results) == [200, 409]
    assert {r["status"] for r in rows(client, scope)} == {"submitted"}
    assert len(audits(client)) == before + 2


def test_week_holds_writer_reservation_through_membership_and_audit(client, scope):
    initial = [create(client, scope)]
    before_select, release_week, insert_attempted = Event(), Event(), Event()
    engine = client.app.state.engine
    with Session(engine) as session:
        employment_before = session.get(Employment, scope[1]).updated_at

    def pause_before_membership(conn, cursor, statement, parameters, context, executemany):
        if (statement.startswith("SELECT overtime_entry.")
                and "overtime_entry.status =" in statement and not before_select.is_set()):
            before_select.set()
            assert release_week.wait(5)
        if statement.startswith("INSERT INTO overtime_entry"):
            insert_attempted.set()

    event.listen(engine, "before_cursor_execute", pause_before_membership)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            transition = pool.submit(weekly, client, scope, initial)
            assert before_select.wait(5)
            new_entry = pool.submit(create, client, scope)
            try:
                assert insert_attempted.wait(5)
                assert not new_entry.done()  # INSERT is serialized behind the confirmed operation.
            finally:
                release_week.set()
            assert transition.result(timeout=5).status_code == 200
            assert new_entry.result(timeout=5)["status"] == "draft"
    finally:
        release_week.set()
        event.remove(engine, "before_cursor_execute", pause_before_membership)
    assert sorted(r["status"] for r in rows(client, scope)) == ["draft", "submitted"]
    with Session(engine) as session:
        assert session.get(Employment, scope[1]).updated_at == employment_before


def test_late_conditional_conflict_rolls_back_prior_rows(client, scope):
    initial = [create(client, scope), create(client, scope)]
    before, before_audits = rows(client, scope), audits(client)
    engine = client.app.state.engine
    updates = 0

    def change_second(conn, cursor, statement, parameters, context, executemany):
        nonlocal updates
        if statement.startswith("UPDATE overtime_entry SET"):
            updates += 1
            if updates == 2:
                # Inject the late mismatch inside the real transaction, after its first row changed.
                conn.exec_driver_sql("UPDATE overtime_entry SET notes = ? WHERE id = ?",
                                     ("synthetic concurrent change", initial[1]["id"]))

    event.listen(engine, "before_cursor_execute", change_second)
    try:
        assert weekly(client, scope, initial).status_code == 409
    finally:
        event.remove(engine, "before_cursor_execute", change_second)
    assert rows(client, scope) == before
    assert audits(client) == before_audits


def test_revision_advances_with_frozen_clock_for_individual_and_week_actions(
        client, scope, monkeypatch):
    initial = [create(client, scope)]
    with Session(client.app.state.engine) as session:
        frozen = session.get(ot.OvertimeEntry, initial[0]["id"]).updated_at.replace(tzinfo=UTC)

    class FrozenClock:
        @staticmethod
        def now(tz):
            return frozen

    monkeypatch.setattr(ot, "datetime", FrozenClock)
    revisions = {initial[0]["revision"]}
    assert weekly(client, scope, initial).status_code == 200
    revisions.add(rows(client, scope)[0]["revision"])
    h, eid = scope
    path = f"/api/v1/employments/{eid}/overtime-entries/{initial[0]['id']}"
    for action in ("reject", "rework"):
        response = client.post(path + "/" + action, headers=h)
        assert response.status_code == 200
        revisions.add(response.json()["revision"])
    response = client.patch(path, headers=h, json={"hours": 2})
    assert response.status_code == 200
    revisions.add(response.json()["revision"])
    assert len(revisions) == 5
    assert weekly(client, scope, initial).status_code == 409


def test_concurrent_edit_after_week_submit_returns_entry_to_draft(client, scope):
    initial = [create(client, scope)]
    before_membership, release_week, edit_attempted = Event(), Event(), Event()
    engine = client.app.state.engine
    h, eid = scope

    def pause_week(conn, cursor, statement, parameters, context, executemany):
        if (statement.startswith("SELECT overtime_entry.")
                and "overtime_entry.status =" in statement and not before_membership.is_set()):
            before_membership.set()
            assert release_week.wait(5)
        if (before_membership.is_set() and not release_week.is_set()
                and statement.startswith(("UPDATE overtime_entry SET", "UPDATE employment SET"))):
            edit_attempted.set()

    event.listen(engine, "before_cursor_execute", pause_week)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            transition = pool.submit(weekly, client, scope, initial)
            assert before_membership.wait(5)
            edit = pool.submit(client.patch,
                               f"/api/v1/employments/{eid}/overtime-entries/{initial[0]['id']}",
                               headers=h, json={"hours": 3})
            try:
                assert edit_attempted.wait(5)
            finally:
                release_week.set()
            assert transition.result(timeout=5).status_code == 200
            assert edit.result(timeout=5).status_code == 200
    finally:
        release_week.set()
        event.remove(engine, "before_cursor_execute", pause_week)
    assert rows(client, scope)[0]["status"] == "draft"
    assert rows(client, scope)[0]["hours"] == "3.00"


def test_weekly_approval_keeps_each_side_of_configured_cutoff_in_its_payroll(client, scope):
    h, eid = scope
    response = client.patch(f"/api/v1/employments/{eid}", headers=h, json={
        "payday_rule": json.dumps({"kind": "fixed_day", "day": 28,
                                   "adjust": "none", "cutoff_day": 15})})
    assert response.status_code == 200, response.text
    initial = [create(client, scope, "2026-04-15"), create(client, scope, "2026-04-16")]
    assert weekly(client, scope, initial, day="2026-04-13").status_code == 200
    assert weekly(client, scope, rows(client, scope), "approve", "2026-04-13").status_code == 200
    for month, start, end, expected in [
        ("04", "2026-03-16", "2026-04-15", initial[0]["pay_pence"]),
        ("05", "2026-04-16", "2026-05-15", initial[1]["pay_pence"]),
    ]:
        run = client.post(f"/api/v1/employments/{eid}/payroll-runs", headers=h, json={
            "period_start": f"2026-{month}-01", "period_end": f"2026-{month}-28",
            "pay_date": f"2026-{month}-28"})
        assert run.status_code == 201, run.text
        body = run.json()
        assert (body["period_start"], body["period_end"]) == (start, end)
        assert [c["forecast_pence"] for c in body["components"]
                if c["kind"] == "overtime"] == [expected]


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("hours,stored", [("2.555", "2.56"), ("2.675", "2.67")])
def test_fresh_week_snapshot_accepts_existing_three_decimal_hours(
        client, scope, method, hours, stored):
    h, eid = scope
    path = f"/api/v1/employments/{eid}/overtime-entries"
    if method == "post":
        response = client.post(path, headers=h, json={"work_date": MONDAY, "hours": hours})
        assert response.status_code == 201
    else:
        initial = create(client, scope)
        response = client.patch(path + f"/{initial['id']}", headers=h, json={"hours": hours})
        assert response.status_code == 200
    before = rows(client, scope)
    assert before[0]["hours"] == stored
    assert response.json()["revision"] == before[0]["revision"]
    assert weekly(client, scope, before).status_code == 200
    after = rows(client, scope)
    assert (after[0]["hours"], after[0]["pay_pence"]) == (
        before[0]["hours"], before[0]["pay_pence"])


def test_deleted_highest_id_reuse_cannot_replay_old_week_snapshot(client, scope):
    first = create(client, scope)
    h, eid = scope
    assert client.delete(f"/api/v1/employments/{eid}/overtime-entries/{first['id']}",
                         headers=h).status_code == 204
    replacement = create(client, scope)
    assert replacement["id"] == first["id"]  # SQLite reuses the highest deleted INTEGER PK.
    assert replacement["revision"] != first["revision"]
    assert weekly(client, scope, [first]).status_code == 409
    assert weekly(client, scope, rows(client, scope)).status_code == 200
