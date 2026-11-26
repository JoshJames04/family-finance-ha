"""Approval protects the agreed Shared schedules from budget changes."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import notifications
from app.main import create_app
from app.persistence.models import NotificationKind, NotificationRule


@pytest.mark.parametrize("earlier_first", [True, False])
def test_budget_change_requires_latest_proposal_approval(
        tmp_path: Path, earlier_first: bool,
) -> None:
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
    with TestClient(create_app(settings)) as client:
        base = "/api/v1"
        household = client.post(f"{base}/households", json={"name": "Test"}).json()["id"]
        headers = {"X-Household-Id": str(household)}

        def post(path: str, payload: dict):
            response = client.post(f"{base}/{path}", headers=headers, json=payload)
            assert response.status_code == 201, response.text
            return response.json()["id"]

        alice = post("profiles", {"display_name": "Alice", "role": "admin"})
        headers["X-Profile-Id"] = str(alice)
        bob = post("profiles", {"display_name": "Bob"})
        viewer = post("profiles", {"display_name": "Viewer", "role": "viewer"})
        today = date.today()

        def account(name: str, owner: int | None = None) -> int:
            return post("accounts", {
                "name": name, "type": "personal" if owner else "joint",
                "owner_profile_id": owner, "opening_balance": 0,
                "opening_date": today.isoformat(),
            })

        a, b, pot = account("Alice Current", alice), account("Bob Current", bob), account("Joint")
        category = post("categories", {"name": "Bills"})
        budget = post("budgets", {
            "name": "Shared", "period_type": "monthly",
            "start_date": today.replace(day=1).isoformat(),
            "end_date": today.replace(day=monthrange(today.year, today.month)[1]).isoformat(),
        })
        line = post(f"budgets/{budget}/lines", {
            "category_id": category, "side": "expense", "amount": 150000,
        })
        assert client.post(f"{base}/budgets/{budget}/approve", headers=headers).status_code == 200
        split = {
            "account_id": pot, "target_pence": 150000,
            "policy": {"mode": "fixed_remainder", "remainder_profile_id": alice},
            "shares": [
                {"profile_id": alice, "source_account_id": a, "day_of_month": 28, "pence": 0},
                {"profile_id": bob, "source_account_id": b, "day_of_month": 11, "pence": 50000},
            ],
        }
        route = f"{base}/transfer-rules/shared-split"
        refused = {**split, "policy": {"mode": "equal", "approval": "delegated",
                                      "approver_profile_id": viewer}}
        assert client.post(route, headers=headers, json=refused).status_code == 422
        assert client.post(route, headers=headers, json=split).status_code == 204

        def revise(amount: int) -> None:
            response = client.patch(f"{base}/budgets/{budget}/lines/{line}", headers=headers,
                                    json={"amount": amount, "reason": "Bills changed"})
            assert response.status_code == 200

        path = f"{base}/transfer-rules/shared-split/{pot}/proposals"
        revise(155000)
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        first, = response.json()
        assert first["old_pence"] == 100000
        assert first["new_pence"] == 105000
        assert first["profile_id"] == alice
        events = client.get(f"{base}/notifications/events", headers=headers).json()
        assert len(events) == 1 and events[0]["payload"]["new_pence"] == 105000
        as_bob = {**headers, "X-Profile-Id": str(bob)}
        assert client.get(path, headers=as_bob).json() == []
        assert client.post(f"{path}/{first['id']}/accept", headers=as_bob).status_code == 403

        revise(157000)
        latest, = client.get(path, headers=headers).json()
        assert latest["new_pence"] == 107000
        assert first["id"] != latest["id"]
        events = client.get(f"{base}/notifications/events", headers=headers).json()
        assert [event["state"] for event in events] == ["pending", "acked"]
        assert client.post(f"{path}/{first['id']}/accept", headers=headers).status_code == 409
        rules = client.get(f"{base}/transfer-rules", headers=headers).json()["items"]
        assert {r["amount_spec"]["pence"] for r in rules} == {50000, 100000}
        future = (today + timedelta(days=90)).replace(day=1)
        future_budget = post("budgets", {
            "name": "Future shared", "period_type": "monthly",
            "start_date": future.isoformat(),
            "end_date": future.replace(day=monthrange(future.year, future.month)[1]).isoformat(),
        })
        post(f"budgets/{future_budget}/lines", {
            "category_id": category, "side": "expense", "amount": 161000,
        })
        assert client.post(f"{base}/budgets/{future_budget}/approve",
                           headers=headers).status_code == 200
        pending = client.get(path, headers=headers).json()
        later = next(item for item in pending if item["budget_id"] == future_budget)
        if not earlier_first:
            assert client.post(f"{path}/{later['id']}/accept", headers=headers).status_code == 204
        for _ in range(2):
            assert client.post(f"{path}/{latest['id']}/accept", headers=headers).status_code == 204
        if earlier_first:
            assert client.post(f"{path}/{later['id']}/accept", headers=headers).status_code == 409
            later, = client.get(path, headers=headers).json()
            assert later["old_pence"] == latest["new_pence"]
            assert client.post(f"{path}/{later['id']}/accept", headers=headers).status_code == 204
        rules = client.get(f"{base}/transfer-rules", headers=headers).json()["items"]
        assert {r["amount_spec"]["pence"] for r in rules} == {50000, 111000}
        revise(49000)
        conflict = next(event["payload"] for event in
                        client.get(f"{base}/notifications/events", headers=headers).json()
                        if event["payload"]["title"] == "Shared contribution split needs updating")
        assert (conflict["fixed_total_pence"], conflict["new_target_pence"]) == (50000, 49000)
        rules = client.get(f"{base}/transfer-rules", headers=headers).json()["items"]
        assert {r["amount_spec"]["pence"] for r in rules} == {50000, 111000}
        for proposal in (latest, later):
            value = client.get(
                f"{base}/transfer-rules/{proposal['rule_id']}/evaluate",
                headers=headers, params={"on": proposal["effective_date"]},
            ).json()
            assert value["amount_pence"] == proposal["new_pence"]

        revise(158000)
        stale = next(item for item in client.get(path, headers=headers).json()
                     if item["budget_id"] == budget)
        edit = client.patch(f"{base}/transfer-rules/{stale['rule_id']}", headers=headers, json={
            "amount_spec": {"kind": "fixed", "pence": 108500},
        })
        assert edit.status_code == 200
        assert all(item["id"] != stale["id"] for item in client.get(path, headers=headers).json())
        events = client.get(f"{base}/notifications/events", headers=headers).json()
        assert next(event for event in events
                    if event["payload"]["id"] == stale["id"])["state"] == "acked"

        revise(159000)
        expiring = next(item for item in client.get(path, headers=headers).json()
                        if item["budget_id"] == budget)
        with Session(client.app.state.engine) as session, session.begin():
            rule = session.scalar(select(NotificationRule).where(
                NotificationRule.kind == NotificationKind.shared_contribution_change,
            ))
            assert rule is not None
            notifications.evaluate(session, rule, today=(
                date.fromisoformat(expiring["effective_date"]) + timedelta(days=1)
            ))
        remaining = client.get(path, headers=headers).json()
        assert all(item["id"] != expiring["id"] for item in remaining)
        events = client.get(f"{base}/notifications/events", headers=headers).json()
        assert next(event for event in events
                    if event["payload"]["id"] == expiring["id"])["state"] == "acked"
