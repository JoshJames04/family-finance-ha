"""Real ingress service reads remain bounded by explicit, revocable grants."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import outbox
from app.main import create_app
from app.persistence.models import Account, AuditEvent, Employment, Household, Role, UserProfile


@pytest.fixture()
def granted_client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.auth.SUPERVISOR_IP", "testclient")
    app = create_app(
        Settings.load(
            {
                "FF_DATA_DIR": str(tmp_path),
                "FF_INGRESS": "true",
                "FF_ADMIN_TOKEN": "synthetic-token",
                "FF_SCHEDULER_TICK_SECONDS": "0",
                "FF_BACKUP_INTERVAL_HOURS": "0",
            }
        )
    )
    with TestClient(app) as client:
        with Session(app.state.engine) as s:
            h = Household(name="Synthetic")
            s.add(h)
            s.flush()
            admin = UserProfile(
                household_id=h.id, display_name="Admin", role=Role.admin, ha_user_id="admin"
            )
            owner = UserProfile(
                household_id=h.id, display_name="Owner", role=Role.member, ha_user_id="owner"
            )
            other = UserProfile(
                household_id=h.id, display_name="Other", role=Role.member, ha_user_id="other"
            )
            s.add_all([admin, owner, other])
            s.flush()
            a = Account(
                household_id=h.id,
                owner_profile_id=owner.id,
                name="Private pay",
                type="personal",
                opening_balance_pence=0,
                opening_date=date.today(),
            )
            s.add(a)
            s.flush()
            e = Employment(
                household_id=h.id,
                profile_id=owner.id,
                employer="Synthetic employer",
                jurisdiction="england_wales",
                contracted_hours=35,
                paid_into_account_id=a.id,
            )
            s.add(e)
            s.flush()
            event = outbox.emit(
                s,
                h.id,
                "payroll.estimate_changed",
                entity_kind="employment",
                entity_id=e.id,
                payload={"date": "2026-11-18", "amount_pence": 12345},
            )
            ids = dict(
                household=h.id,
                owner=owner.id,
                other=other.id,
                account=a.id,
                employment=e.id,
                event=event.id,
            )
            s.commit()
        yield client, app, ids


ADMIN = {"X-Remote-User-Id": "admin"}
SERVICE = {"X-Admin-Token": "synthetic-token"}
PATH = "/api/v1/integration/private-grants"


def test_service_grants_default_deny_opt_in_redact_and_revoke(granted_client):
    client, app, ids = granted_client

    def events(headers=SERVICE):
        return client.get("/api/v1/integration/events", headers=headers).json()["events"]

    assert events() == []
    body = {"profile_ids": [ids["owner"]], "account_ids": []}
    assert client.put(PATH, headers=ADMIN, json=body).status_code == 200
    assert events() == []  # payroll also needs its private destination grant
    body["account_ids"] = [ids["account"]]
    response = client.put(PATH, headers=ADMIN, json=body)
    assert response.status_code == 200, response.text
    assert [e["seq"] for e in events()] == [ids["event"]]
    assert events()[0]["data"] == {"date": "2026-11-18"}
    assert (
        client.patch(
            "/api/v1/integration/settings", headers=ADMIN, json={"detail": "detailed"}
        ).status_code
        == 200
    )
    assert events()[0]["data"] == {"date": "2026-11-18", "amount_pence": 12345}
    assert (
        client.get("/api/v1/integration/events?latest=true", headers=SERVICE).json()["next_cursor"]
        == ids["event"]
    )
    # Neither a member nor anonymous service/header spoof inherits the machine's grant.
    assert events({"X-Remote-User-Id": "other"}) == []
    assert events({**SERVICE, "X-Profile-Id": str(ids["other"])}) == events()
    assert (
        client.put(PATH, headers=ADMIN, json={"profile_ids": [], "account_ids": []}).status_code
        == 200
    )
    assert events() == []
    assert (
        client.get("/api/v1/integration/events?latest=true", headers=SERVICE).json()["next_cursor"]
        == 0
    )
    with Session(app.state.engine) as s:
        audit = list(s.scalars(select(AuditEvent).where(AuditEvent.action == "integration_grants")))
        assert len(audit) == 3
        assert all(e.actor == "profile:1" for e in audit)


def test_grant_management_requires_real_admin_and_valid_named_resources(granted_client):
    client, _, ids = granted_client
    body = {"profile_ids": [ids["owner"]], "account_ids": [ids["account"]]}
    for headers in [SERVICE, {"X-Remote-User-Id": "owner"}, {**SERVICE, "X-Profile-Id": "1"}]:
        assert client.put(PATH, headers=headers, json=body).status_code == 403
    assert client.get(PATH, headers=ADMIN).json()["profiles"][1]["id"] == ids["owner"]
    assert client.put(PATH, headers=ADMIN, json={**body, "account_ids": [999]}).status_code == 422
    assert client.put(PATH, headers=ADMIN, json={**body, "profile_ids": [999]}).status_code == 422
    assert (
        client.get("/api/v1/integration/events", headers={"X-Admin-Token": "wrong"}).status_code
        == 401
    )
    assert client.get("/api/v1/employments", headers=SERVICE).status_code == 403
    assert client.post(
        f"/api/v1/employments/{ids['employment']}/overtime-entries", headers=SERVICE, json={}
    ).status_code in {403, 422}


@pytest.mark.parametrize("change", ["profile", "account", "deactivate"])
def test_ownership_and_active_identity_changes_do_not_reveal_history(granted_client, change):
    client, app, ids = granted_client
    body = {"profile_ids": [ids["owner"], ids["other"]], "account_ids": [ids["account"]]}
    assert client.put(PATH, headers=ADMIN, json=body).status_code == 200
    with Session(app.state.engine) as s:
        if change == "profile":
            s.get(Employment, ids["employment"]).profile_id = ids["other"]
        elif change == "account":
            s.get(Account, ids["account"]).owner_profile_id = ids["other"]
        else:
            s.get(UserProfile, ids["owner"]).is_active = False
        s.commit()
    assert client.get("/api/v1/integration/events", headers=SERVICE).json()["events"] == []
    if change == "profile":
        assert (
            client.get("/api/v1/integration/events", headers={"X-Remote-User-Id": "other"}).json()[
                "events"
            ]
            == []
        )


def test_standalone_service_uses_token_not_compat_profile_header(granted_client):
    client, app, ids = granted_client
    body = {"profile_ids": [ids["owner"]], "account_ids": [ids["account"]]}
    assert client.put(PATH, headers=ADMIN, json=body).status_code == 200
    app.state.settings.ingress = False
    assert client.get("/api/v1/integration/events").json()["events"] == []
    assert (
        client.get("/api/v1/integration/events", headers=SERVICE).json()["events"][0]["seq"]
        == ids["event"]
    )
    assert (
        client.get(
            "/api/v1/integration/events", headers={**SERVICE, "X-Profile-Id": str(ids["other"])}
        ).json()["events"][0]["seq"]
        == ids["event"]
    )
    assert client.put(PATH, headers={**SERVICE, "X-Profile-Id": "1"}, json=body).status_code == 403


def test_human_payroll_mutations_require_ownership_even_for_admin(granted_client):
    client, _, ids = granted_client
    path = f"/api/v1/employments/{ids['employment']}"
    assert client.get(path, headers=ADMIN).status_code == 200
    assert client.patch(path, headers=ADMIN, json={"employer": "Changed"}).status_code == 403
    assert (
        client.patch(
            path, headers={"X-Remote-User-Id": "owner"}, json={"employer": "Own employer"}
        ).status_code
        == 200
    )
    body = {"profile_id": ids["owner"], "employer": "New", "contracted_hours": 35}
    assert client.post("/api/v1/employments", headers=ADMIN, json=body).status_code == 403
    body["profile_id"] = 1
    assert client.post("/api/v1/employments", headers=ADMIN, json=body).status_code == 201
    prepared = client.post("/api/v1/employments/prepare-year", headers=ADMIN, json={"year": 2027})
    assert prepared.status_code == 201
    assert not any("Own employer" in warning for warning in prepared.json()["warnings"])


def test_payroll_grants_never_grant_private_bank_reads_or_writes(granted_client):
    client, app, ids = granted_client
    body = {"profile_ids": [ids["owner"]], "account_ids": [ids["account"]]}
    assert client.put(PATH, headers=ADMIN, json=body).status_code == 200
    with Session(app.state.engine) as s:
        outbox.emit(
            s,
            ids["household"],
            "balance.crossed",
            entity_kind="account",
            entity_id=ids["account"],
            payload={"amount_pence": 100},
        )
        s.commit()
    feed = client.get("/api/v1/integration/events", headers=SERVICE).json()["events"]
    assert [e["event_type"] for e in feed] == ["payroll.estimate_changed"]
    assert client.get("/api/v1/integration/items", headers=SERVICE).json()["items"] == []
    assert (
        client.get(f"/api/v1/accounts/{ids['account']}/balance", headers=SERVICE).status_code == 404
    )
    overtime_path = f"/api/v1/employments/{ids['employment']}/overtime-entries"
    assert (
        client.post(
            overtime_path, headers=SERVICE, json={"work_date": "2026-11-18", "hours": 1}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/employments/{ids['employment']}",
            headers=SERVICE,
            json={"employer": "Injected"},
        ).status_code
        == 403
    )


def test_grants_cannot_select_other_household_resources(granted_client):
    client, app, _ = granted_client
    with Session(app.state.engine) as s:
        h = Household(name="Other household")
        s.add(h)
        s.flush()
        p = UserProfile(household_id=h.id, display_name="Elsewhere", role=Role.member)
        s.add(p)
        s.flush()
        pid = p.id
        s.commit()
    assert (
        client.put(PATH, headers=ADMIN, json={"profile_ids": [pid], "account_ids": []}).status_code
        == 422
    )


def test_payday_discovery_requires_grants_keeps_legacy_prefix_and_revokes(
    granted_client, monkeypatch
):
    from app.api.v1 import integration

    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 18)

    monkeypatch.setattr(integration, "date", Today)
    client, app, ids = granted_client
    with Session(app.state.engine) as s:
        s.get(Employment, ids["employment"]).payday_rule = '{"kind":"fixed_day","day":22}'
        shared = Account(
            household_id=ids["household"],
            name="Shared",
            type="joint",
            opening_date=date(2026, 1, 1),
        )
        s.add(shared)
        s.commit()
    path = "/api/v1/integration/items"
    baseline = client.get(path, headers=SERVICE).json()["items"]
    assert baseline[0]["family"] == "accounts"
    assert not any(
        item["family"] == "paydays" or item["id"] == f"account:{ids['account']}"
        for item in baseline
    )
    assert (
        client.put(
            PATH, headers=ADMIN, json={"profile_ids": [ids["owner"]], "account_ids": []}
        ).status_code
        == 200
    )
    assert client.get(path, headers=SERVICE).json()["items"] == baseline
    grant = {"profile_ids": [ids["owner"]], "account_ids": [ids["account"]]}
    assert client.put(PATH, headers=ADMIN, json=grant).status_code == 200
    result = client.get(path, headers={**SERVICE, "X-Profile-Id": str(ids["other"])}).json()[
        "items"
    ]
    assert result[:-1] == baseline
    assert result[-1] == {
        "family": "paydays",
        "id": f"employment:{ids['employment']}",
        "name": "Synthetic employer · Next payday",
        "value": 2,
        "unit": "d",
    }
    assert client.get(path + "?limit=1", headers=SERVICE).json()["items"] == baseline[:1]
    assert client.get(path, headers={"X-Remote-User-Id": "other"}).json()["items"] == baseline
    assert (
        client.put(PATH, headers=ADMIN, json={"profile_ids": [], "account_ids": []}).status_code
        == 200
    )
    assert client.get(path, headers=SERVICE).json()["items"] == baseline


@pytest.mark.parametrize("change", ["owner", "destination_owner", "end", "start", "inactive"])
def test_payday_discovery_rechecks_current_scope_and_employment_dates(granted_client, change):
    client, app, ids = granted_client
    with Session(app.state.engine) as s:
        s.get(Employment, ids["employment"]).payday_rule = '{"kind":"fixed_day","day":22}'
        s.commit()
    grant = {"profile_ids": [ids["owner"]], "account_ids": [ids["account"]]}
    assert client.put(PATH, headers=ADMIN, json=grant).status_code == 200
    assert client.get("/api/v1/integration/items", headers=SERVICE).json()["items"]
    with Session(app.state.engine) as s:
        e = s.get(Employment, ids["employment"])
        if change == "owner":
            e.profile_id = ids["other"]
        elif change == "destination_owner":
            s.get(Account, ids["account"]).owner_profile_id = ids["other"]
        elif change == "end":
            e.end_date = date(2020, 1, 1)
        elif change == "start":
            e.start_date = date(2030, 1, 1)
        else:
            e.is_active = False
        s.commit()
    assert client.get("/api/v1/integration/items", headers=SERVICE).json()["items"] == []
    if change in {"owner", "destination_owner"}:
        # Discovery is current state: an explicit fresh grant can expose the
        # current payday, while historical event snapshots retain their scope.
        grant["profile_ids"] = [ids["other"] if change == "owner" else ids["owner"]]
        assert client.put(PATH, headers=ADMIN, json=grant).status_code == 200
        current = client.get("/api/v1/integration/items", headers=SERVICE).json()["items"]
        assert current[0]["id"] == f"employment:{ids['employment']}"


def test_owner_keeps_historical_events_after_changing_pay_destination(granted_client):
    client, app, ids = granted_client
    with Session(app.state.engine) as s:
        account = Account(
            household_id=ids["household"],
            name="New pay destination",
            owner_profile_id=ids["owner"],
            type="personal",
            opening_date=date.today(),
        )
        s.add(account)
        s.flush()
        s.get(Employment, ids["employment"]).paid_into_account_id = account.id
        s.commit()
    owner = {"X-Remote-User-Id": "owner"}
    events = client.get("/api/v1/integration/events", headers=owner).json()["events"]
    assert [event["seq"] for event in events] == [ids["event"]]
    assert client.get("/api/v1/integration/events", headers=SERVICE).json()["events"] == []


@pytest.mark.parametrize("environment,expected", [("dev", 200), ("prod", 403), ("ci", 403)])
def test_standalone_dev_grant_settings_accept_only_explicit_admin_compat(
    granted_client, environment, expected
):
    client, app, ids = granted_client
    app.state.settings.ingress = False
    app.state.settings.env = environment
    body = {"profile_ids": [ids["owner"]], "account_ids": [ids["account"]]}
    admin = {"X-Profile-Id": "1"}
    assert client.get(PATH, headers=admin).status_code == expected
    assert client.put(PATH, headers=admin, json=body).status_code == expected
    assert client.get(PATH, headers={"X-Profile-Id": str(ids["owner"])}).status_code == 403
    assert client.put(PATH, headers={**SERVICE, **admin}, json=body).status_code == 403
    assert client.get(PATH, headers={**SERVICE, **admin}).status_code == 403
