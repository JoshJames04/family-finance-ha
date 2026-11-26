"""The funding suggestion uses dated liabilities and avoids duplicate top-ups."""
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import Confidence, ExpectedMovement, ExpectedSource


def test_bills_space_funding_window_and_one_off(tmp_path: Path) -> None:
    app = create_app(Settings.load({"FF_DATA_DIR": str(tmp_path)}))
    today = date.today()
    due = today + timedelta(days=1)
    with TestClient(app) as client:
        base = "/api/v1"
        household = client.post(f"{base}/households", json={"name": "Test"}).json()["id"]
        h = {"X-Household-Id": str(household)}

        def account(name: str, kind: str, balance: int) -> int:
            response = client.post(f"{base}/accounts", headers=h, json={
                "name": name, "type": kind, "opening_balance": balance,
                "opening_date": today.isoformat(),
            })
            assert response.status_code == 201, response.text
            return int(response.json()["id"])

        profile = client.post(f"{base}/profiles", headers=h, json={
            "display_name": "Admin", "role": "admin",
        }).json()["id"]
        h["X-Profile-Id"] = str(profile)
        joint = account("Joint", "joint", 60000)
        bills = account("Bills", "bills", 2000)
        response = client.post(f"{base}/transfer-rules", headers=h, json={
            "name": "Standing top-up", "source_account_id": joint, "dest_account_id": bills,
            "amount_spec": {"kind": "fixed", "pence": 10000},
            "frequency": "monthly", "day_of_month": due.day, "start_date": due.isoformat(),
        })
        assert response.status_code == 201, response.text
        standing_id = response.json()["id"]
        with Session(app.state.engine) as session, session.begin():
            for key, day, amount in [(1, today, -5000), (2, due, -4000), (3, due, -3000)]:
                session.add(ExpectedMovement(
                    household_id=household, account_id=bills, expected_date=day,
                    label=f"Bill {key}", source_type=ExpectedSource.commitment, source_id=key,
                    amount_pence=amount, confidence=Confidence.scheduled,
                ))

        route = f"{base}/transfer-rules/bills-space/{bills}"
        joint_funding = client.get(f"{base}/transfer-rules/bills-space/{joint}", headers=h)
        assert joint_funding.status_code == 404
        default = client.get(route, headers=h).json()
        assert default["top_up_pence"] == 10000 and default["next_top_up"] == due.isoformat()
        assert default["first_shortfall"] == today.isoformat()
        buffered = client.get(route, headers=h, params={"buffer_pence": 1500}).json()
        assert buffered["top_up_pence"] == 11500
        explicit = client.get(route, headers=h, params={"through": due.isoformat()}).json()
        assert explicit["top_up_pence"] == 10000
        assert any(row["pence"] == 10000 for row in explicit["movements"])

        body = {"source_account_id": joint, "amount_pence": 10000, "buffer_pence": 0}
        accepted = client.post(route, headers=h, json=body)
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["start_date"] == accepted.json()["end_date"] == today.isoformat()
        assert client.get(route, headers=h).json()["top_up_pence"] == 0
        assert client.post(route, headers=h, json=body).status_code == 409
        rules = client.get(f"{base}/transfer-rules", headers=h).json()["items"]
        assert next(r for r in rules if r["id"] == standing_id)["end_date"] is None

        pending = client.post(f"{route}/standing", headers=h, json={
            "rule_id": standing_id, "amount_pence": 14000,
        })
        assert pending.status_code == 201, pending.text
        proposal = pending.json()
        assert proposal["state"] == "pending"
        assert proposal["effective_date"] == due.isoformat()
        assert client.get(route, headers=h).json()["standing_pence"] == 10000
        inbox = client.get(f"{base}/notifications/events", headers=h).json()
        assert any(event["payload"]["title"] == "Bills account top-up change awaiting approval"
                   for event in inbox)
        decisions = f"{base}/transfer-rules/shared-split/{bills}/proposals/{proposal['id']}"
        assert client.post(f"{decisions}/accept", headers=h).status_code == 204
        assert client.get(route, headers=h).json()["standing_pence"] == 14000
