"""Caller roles propagate to private integration and banking monitoring."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import outbox
from app.main import create_app
from app.persistence.models import Account, Household, SyncRun, UserProfile


@pytest.mark.parametrize("resource_kind", ["sync", "events"])
def test_private_monitoring_respects_admin_owner_other_member_and_viewer(tmp_path, resource_kind):
    app = create_app(Settings.load({
        "FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0",
        "FF_BACKUP_INTERVAL_HOURS": "0",
    }))
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            home, foreign = Household(name="Home"), Household(name="Other")
            session.add_all([home, foreign])
            session.flush()
            profiles = [UserProfile(household_id=home.id, display_name=str(i), role=role)
                        for i, role in enumerate(("admin", "member", "member", "viewer"))]
            session.add_all(profiles)
            session.flush()
            ids, hid = [p.id for p in profiles], home.id
            accounts = [Account(household_id=hh, owner_profile_id=owner, name=str(i),
                                type="personal", opening_date=date(2025, 1, 1))
                        for i, (hh, owner) in enumerate([
                            (hid, None), (hid, ids[1]), (foreign.id, None),
                        ])]
            session.add_all(accounts)
            session.flush()
            account_ids = [a.id for a in accounts]
            event_ids = []
            for account in accounts:
                session.add(SyncRun(account_id=account.id, source="starling"))
                event = outbox.emit(session, account.household_id, "data.stale",
                                    entity_kind="account", entity_id=account.id,
                                    payload={"amount_pence": 999, "status": "stale"})
                assert event is not None
                event_ids.append(event.id)
            session.commit()
        # The admin is deliberately not the owner of the private account.
        for index, profile_id in enumerate([*ids, None]):
            headers = {"X-Household-Id": str(hid)}
            if profile_id is not None:
                headers["X-Profile-Id"] = str(profile_id)
            allowed_private = index in (0, 1)
            if resource_kind == "events":
                visible = event_ids[:2] if allowed_private else event_ids[:1]
                feed = client.get("/api/v1/integration/events", headers=headers).json()
                assert [e["seq"] for e in feed["events"]] == visible
                assert all("amount_pence" not in e["data"] for e in feed["events"])
                latest = client.get(
                    "/api/v1/integration/events?latest=true", headers=headers).json()
                assert latest["events"] == []
                assert latest["next_cursor"] == visible[-1]
                continue
            for account_id, allowed in zip(
                    account_ids, [True, allowed_private, False], strict=True):
                for resource in ("runs", "cursor"):
                    response = client.get(f"/api/v1/sync/accounts/{account_id}/{resource}",
                                          headers=headers)
                    assert response.status_code == (200 if allowed else 404)
                    if resource == "runs" and allowed:
                        assert [r["account_id"] for r in response.json()] == [account_id]
