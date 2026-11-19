"""Explicit occasion dates enable reminders without guessing historic dates."""

import sqlite3
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.settings import Settings
from app.domain import financial_events, outbox, seasonal
from app.main import create_app
from app.persistence.backup import create_snapshot, restore_from, verify_snapshot
from app.persistence.models import IntegrationEvent, Role, SeasonalEvent, UserProfile


def test_create_edit_clear_and_validate_year_without_changing_other_data(tmp_path):
    with TestClient(create_app(Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as client:
        hid = client.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
        headers = {"X-Household-Id": str(hid)}
        body = {"name": "Synthetic occasion", "type": "other", "year": 2026,
                "budget": 10000, "notes": "Private note", "event_date": "2026-12-20"}
        response = client.post("/api/v1/seasonal-events", headers=headers, json=body)
        assert response.status_code == 201
        event = response.json()
        assert event["event_date"] == "2026-12-20"
        route = f"/api/v1/seasonal-events/{event['id']}/date"
        assert client.patch(route, headers=headers, json={"event_date": "2027-01-01"}
                            ).status_code == 422
        changed = client.patch(route, headers=headers, json={"event_date": "2026-12-21"})
        assert changed.status_code == 200
        assert changed.json()["event_date"] == "2026-12-21"
        assert changed.json()["budget_pence"] == 10000
        assert changed.json()["notes"] == "Private note"
        assert client.patch(route, headers=headers, json={"event_date": None}
                            ).json()["event_date"] is None
        assert client.patch(route, headers={"X-Household-Id": str(hid + 1)},
                            json={"event_date": "2026-12-21"}).status_code == 404
        body["type"] = "birthday"
        body["event_date"] = "2027-01-01"
        assert client.post("/api/v1/seasonal-events", headers=headers, json=body
                           ).status_code == 422


def test_reminder_uses_explicit_date_catches_up_and_deduplicates(session, household):
    due = date(2026, 12, 20)
    event = SeasonalEvent(household_id=household.id, year=2026, type="other",
                          name="Synthetic occasion", budget_pence=10000,
                          notes="Never exported", event_date=due)
    unknown = SeasonalEvent(household_id=household.id, year=2026, type="christmas",
                            name="Unknown date", budget_pence=10000)
    session.add_all([event, unknown])
    session.flush()
    financial_events.evaluate_seasonal(session, household.id, today=due - timedelta(days=4))
    assert list(session.scalars(select(IntegrationEvent))) == []
    # First available tick inside the window is a catch-up, not a missed event.
    financial_events.evaluate_seasonal(session, household.id, today=due - timedelta(days=1))
    financial_events.evaluate_seasonal(session, household.id, today=due)
    rows = list(session.scalars(select(IntegrationEvent)))
    assert len(rows) == 1 and rows[0].event_type == "seasonal.approaching"
    assert rows[0].entity_id == event.id
    assert outbox.visible_payload(rows[0], False) == {
        "source": "seasonal", "status": "approaching", "date": "2026-12-19",
        "expected_date": "2026-12-20", "lead_days": 3}
    assert outbox.visible_payload(rows[0], True)["name"] == "Synthetic occasion"
    assert "notes" not in rows[0].payload
    session.rollback()
    assert list(session.scalars(select(IntegrationEvent))) == []


def test_past_date_and_rollover_do_not_invent_new_reminders(session, household):
    event = SeasonalEvent(household_id=household.id, year=2026, type="other",
                          name="Synthetic occasion", budget_pence=10000,
                          event_date=date(2026, 12, 20))
    session.add(event)
    session.flush()
    financial_events.evaluate_seasonal(session, household.id, today=date(2026, 12, 21))
    assert list(session.scalars(select(IntegrationEvent))) == []
    rolled = seasonal.rollover(session, event, 2027)
    assert rolled.event_date is None


def test_private_reminder_retains_original_scope_after_reclassification(session, household):
    owner = UserProfile(household_id=household.id, display_name="Synthetic owner", role=Role.member)
    session.add(owner)
    session.flush()
    event = SeasonalEvent(household_id=household.id, year=2026, type="other",
                          name="Synthetic private occasion", budget_pence=10000,
                          event_date=date(2026, 12, 20), owner_profile_id=owner.id)
    session.add(event)
    session.flush()
    financial_events.evaluate_seasonal(session, household.id, today=date(2026, 12, 19))
    row = session.scalar(select(IntegrationEvent))
    assert outbox.event_visible(session, row, owner.id)
    assert not outbox.event_visible(session, row, None)
    event.owner_profile_id = None
    session.flush()
    assert not outbox.event_visible(session, row, None)
    assert "_scope_owner_profile_id" not in outbox.visible_payload(row, True)


def test_date_patch_rejects_viewers_and_other_members(tmp_path):
    with TestClient(create_app(Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as client:
        hid = client.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
        headers = {"X-Household-Id": str(hid)}
        owner = client.post("/api/v1/profiles", headers=headers,
                            json={"display_name": "Synthetic owner", "role": "member"}).json()
        viewer = client.post("/api/v1/profiles", headers=headers,
                             json={"display_name": "Synthetic viewer", "role": "viewer"}).json()
        member = client.post("/api/v1/profiles", headers=headers,
                             json={"display_name": "Synthetic other", "role": "member"}).json()
        own = {**headers, "X-Profile-Id": str(owner["id"])}
        body = {"name": "Private occasion", "type": "other", "year": 2026,
                "budget": 10000, "owner_profile_id": owner["id"]}
        created = client.post("/api/v1/seasonal-events", headers=own, json=body)
        assert created.status_code == 201
        route = f"/api/v1/seasonal-events/{created.json()['id']}/date"
        assert client.patch(route, headers={**headers, "X-Profile-Id": str(member["id"])},
                            json={"event_date": "2026-12-20"}).status_code == 404
        body.update(type="shared", owner_profile_id=None)
        shared = client.post("/api/v1/seasonal-events", headers=own, json=body).json()
        assert client.patch(f"/api/v1/seasonal-events/{shared['id']}/date",
                            headers={**headers, "X-Profile-Id": str(viewer["id"])},
                            json={"event_date": "2026-12-20"}).status_code == 403


def test_explicit_date_survives_normal_snapshot_restore(session, household, tmp_path):
    session.add(SeasonalEvent(household_id=household.id, year=2026, type="other",
                             name="Synthetic occasion", budget_pence=10000,
                             event_date=date(2026, 12, 20)))
    session.commit()
    snapshot = create_snapshot(session.get_bind(), tmp_path / "backups")
    assert verify_snapshot(snapshot)
    restored = tmp_path / "restored.db"
    restore_from(snapshot, restored)
    with sqlite3.connect(restored) as conn:
        assert conn.execute("SELECT event_date FROM seasonal_event").fetchone()[0] == "2026-12-20"
