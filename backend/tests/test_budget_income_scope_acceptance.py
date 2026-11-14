"""Budget income classification regressions use disposable synthetic databases."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"})
    with TestClient(create_app(settings)) as client:
        household = client.post("/api/v1/households", json={"name": "Synthetic"}).json()
        client.headers["X-Household-Id"] = str(household["id"])
        admin = client.post(
            "/api/v1/profiles", json={"display_name": "Synthetic admin", "role": "admin"}
        ).json()
        client.headers["X-Profile-Id"] = str(admin["id"])
        yield client


def post(client, path, body):
    response = client.post("/api/v1/" + path, json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("planned_side", ["income", "expense", None])
def test_receipt_does_not_turn_into_refund_when_all_adds_another_account(client, planned_side):
    """A debit in the same income category must not erase an earned receipt."""
    today = date.today().isoformat()
    admin = int(client.headers["X-Profile-Id"])
    member = post(client, "profiles", {"display_name": "Synthetic member", "role": "member"})
    viewer = post(client, "profiles", {"display_name": "Synthetic viewer", "role": "viewer"})
    category = post(client, "categories", {"name": "Synthetic income"})
    budget_ids = []
    receipt_id = None
    for owner, amount in [(admin, 24000), (member["id"], -3000), (None, 0)]:
        account = post(client, "accounts", {
            "name": f"Synthetic account {owner}", "type": "personal" if owner else "joint",
            "owner_profile_id": owner, "opening_balance": 0, "opening_date": today,
        })
        if amount:
            row = post(client, "transactions", {
                "account_id": account["id"], "date": today, "amount": amount,
                "payee": "Synthetic payer", "category_id": category["id"],
            })
            if amount > 0:
                receipt_id = row["id"]
        budget = post(client, "budgets", {
            "name": "Synthetic plan", "owner_profile_id": owner, "period_type": "custom",
            "start_date": today, "end_date": today,
        })
        if planned_side:
            post(client, f"budgets/{budget['id']}/lines", {
                "category_id": category["id"], "side": planned_side, "amount": 30000,
            })
        budget_ids.append(budget["id"])

    personal = client.get(f"/api/v1/budgets/{budget_ids[0]}/actuals")
    assert personal.status_code == 200, personal.text
    assert personal.json()["income_received_pence"] == 24000
    shared = client.get(f"/api/v1/budgets/{budget_ids[2]}/actuals")
    assert shared.status_code == 200, shared.text
    assert shared.json()["income_received_pence"] == 0
    assert shared.json()["spent_pence"] == 0
    combined_url = f"/api/v1/budgets/reports/combined?start={today}&end={today}"
    combined = client.get(combined_url)
    assert combined.status_code == 200, combined.text
    actuals = combined.json()["actuals"]
    assert actuals["income_received_pence"] == 24000
    assert actuals["spent_pence"] == 3000
    assert receipt_id not in {item["transaction_id"] for item in actuals["spend_items"]}

    # The same real route must continue to use only the caller's visible ledger.
    restricted = client.get(combined_url, headers={"X-Profile-Id": str(member["id"])})
    assert restricted.status_code == 200, restricted.text
    assert restricted.json()["actuals"]["income_received_pence"] == 0
    assert restricted.json()["actuals"]["spent_pence"] == 3000
    assert client.get(
        f"/api/v1/budgets/{budget_ids[0]}/actuals",
        headers={"X-Profile-Id": str(member["id"])},
    ).status_code == 404
    shared_only = client.get(combined_url, headers={"X-Profile-Id": str(viewer["id"])})
    assert shared_only.status_code == 200, shared_only.text
    assert shared_only.json()["actuals"]["income_received_pence"] == 0
    assert shared_only.json()["actuals"]["spent_pence"] == 0




def test_linked_refund_remains_expense_flow_when_category_is_planned_income(session, household):
    from app.domain import budgets
    from app.persistence.models import (
        Account,
        BudgetLineSide,
        BudgetPeriodType,
        Category,
        Transaction,
    )

    today = date.today()
    account = Account(
        household_id=household.id, name="Synthetic account", type="joint", opening_date=today,
    )
    category = Category(household_id=household.id, name="Synthetic category")
    session.add_all([account, category])
    session.flush()
    budget = budgets.create_budget(
        session, household.id, "Synthetic plan", BudgetPeriodType.custom, today, today,
    )
    budgets.add_line(session, budget, category.id, BudgetLineSide.income, 20000)
    original = Transaction(
        account_id=account.id, date=today, amount_pence=-6000, payee="Synthetic shop",
        category_id=category.id,
    )
    session.add(original)
    session.flush()
    refund = Transaction(
        account_id=account.id, date=today, amount_pence=2000, payee="Synthetic refund",
        category_id=category.id, refund_of_id=original.id,
    )
    session.add(refund)
    session.flush()
    actuals = budgets.spending_summary(session, budget)
    assert actuals["income_received_pence"] == 0
    assert actuals["spent_pence"] == 4000
    assert {item["transaction_id"] for item in actuals["spend_items"]} == {
        original.id, refund.id,
    }



def test_shared_budget_and_expected_stream_use_shared_accounts_while_all_uses_union(client):
    from sqlalchemy.orm import Session

    from app.persistence.models import Confidence, ExpectedMovement, ExpectedSource

    today = date.today().isoformat()
    owner = int(client.headers["X-Profile-Id"])
    category = post(client, "categories", {"name": "Synthetic spending"})
    budget_ids = []
    expected_ids = []
    for scope, amount in [(None, 1100), (owner, 2200)]:
        account = post(client, "accounts", {
            "name": f"Synthetic {scope}", "type": "joint" if scope is None else "personal",
            "owner_profile_id": scope, "opening_balance": 0, "opening_date": today,
        })
        post(client, "transactions", {
            "account_id": account["id"], "date": today, "amount": -amount,
            "payee": "Synthetic shop", "category_id": category["id"],
        })
        budget = post(client, "budgets", {
            "name": f"Synthetic plan {scope}", "owner_profile_id": scope,
            "period_type": "custom", "start_date": today, "end_date": today,
        })
        line = post(client, f"budgets/{budget['id']}/lines", {
            "category_id": category["id"], "side": "expense", "amount": 5000,
        })
        budget_ids.append((budget["id"], line["id"]))
        with Session(client.app.state.engine) as session:
            movement = ExpectedMovement(
                household_id=int(client.headers["X-Household-Id"]), account_id=account["id"],
                expected_date=date.today(), amount_pence=-amount, label="Synthetic bill",
                confidence=Confidence.scheduled, source_type=ExpectedSource.subscription,
                source_id=account["id"],
            )
            session.add(movement)
            session.commit()
            expected_ids.append(movement.id)

    for (bid, line_id), amount, movement_id in zip(
        budget_ids, [1100, 2200], expected_ids, strict=True,
    ):
        actuals = client.get(f"/api/v1/budgets/{bid}/actuals").json()
        assert actuals["spent_pence"] == amount
        assert actuals["totals"][str(line_id)] == amount
        assert actuals["bills_remaining_pence"] == amount
        assert actuals["bills_remaining_count"] == 1
        expected = client.get(
            f"/api/v1/expected-movements?start={today}&end={today}&budget_id={bid}",
        )
        assert expected.status_code == 200, expected.text
        assert [row["id"] for row in expected.json()] == [movement_id]

    combined = client.get(
        f"/api/v1/budgets/reports/combined?start={today}&end={today}",
    ).json()["actuals"]
    assert combined["spent_pence"] == 3300
    assert sum(combined["totals"].values()) == 3300
    assert combined["bills_remaining_pence"] == 3300
    assert combined["bills_remaining_count"] == 2
    assert len(combined["spend_items"]) == 2
    report = client.get(
        f"/api/v1/reports/monthly?year={date.today().year}&month={date.today().month}",
    ).json()
    shared_id, shared_line = budget_ids[0]
    figure = next(
        figure for section in report["sections"] for figure in section["figures"]
        if figure["key"] == f"budget_{shared_id}_line_{shared_line}"
    )
    assert figure["drill"]["params"]["shared_only"] is True
    drill = client.get(figure["drill"]["endpoint"], params=figure["drill"]["params"])
    assert drill.status_code == 200, drill.text
    assert [row["amount_pence"] for row in drill.json()["items"]] == [-1100]


def test_salary_forecast_exposes_partial_paid_and_dismissed_residuals(client):
    from datetime import datetime

    from sqlalchemy.orm import Session

    from app.persistence.models import Confidence, ExpectedMovement, ExpectedSource

    today = date.today().isoformat()
    owner = int(client.headers["X-Profile-Id"])
    account = post(client, "accounts", {
        "name": "Synthetic salary account", "type": "personal", "owner_profile_id": owner,
        "opening_balance": 0, "opening_date": today,
    })
    budget = post(client, "budgets", {
        "name": "Synthetic salary plan", "owner_profile_id": owner, "period_type": "custom",
        "start_date": today, "end_date": today,
    })
    ids = []
    for index, paid in enumerate([0, 10000, 20000, None]):
        with Session(client.app.state.engine) as session:
            movement = ExpectedMovement(
                household_id=int(client.headers["X-Household-Id"]), account_id=account["id"],
                expected_date=date.today(), amount_pence=20000, label="Synthetic salary",
                confidence=Confidence.scheduled, source_type=ExpectedSource.payroll_run,
                source_id=index + 1, dismissed_at=datetime.now() if paid is None else None,
            )
            session.add(movement)
            session.commit()
            ids.append(movement.id)
        if paid:
            receipt = post(client, "transactions", {
                "account_id": account["id"], "date": today, "amount": paid,
                "payee": "Synthetic employer",
            })
            post(client, f"expected-movements/{ids[-1]}/matches", {
                "transaction_id": receipt["id"],
            })
    response = client.get(
        f"/api/v1/expected-movements?start={today}&end={today}&budget_id={budget['id']}",
    )
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()}
    assert [rows[mid]["outstanding_pence"] for mid in ids[:3]] == [20000, 10000, 0]
    assert rows[ids[3]]["dismissed_at"] is not None
    # Expected rows remain history; residual and dismissal tell the UI what is still due.
    assert [rows[mid]["amount_pence"] for mid in ids] == [20000, 20000, 20000, 20000]
    received = client.get(f"/api/v1/budgets/{budget['id']}/actuals").json()
    assert received["income_received_pence"] == 30000



def test_all_includes_visible_accounts_without_a_plan_in_selected_period(client):
    from datetime import timedelta

    from sqlalchemy.orm import Session

    from app.persistence.models import Confidence, ExpectedMovement, ExpectedSource

    today = date.today().isoformat()
    admin = int(client.headers["X-Profile-Id"])
    member = post(client, "profiles", {"display_name": "Synthetic member", "role": "member"})
    category = post(client, "categories", {"name": "Synthetic expense"})
    for owner, amount in [(admin, 1000), (member["id"], 2000), (None, 4000)]:
        account = post(client, "accounts", {
            "name": f"Synthetic account {owner}", "type": "personal" if owner else "joint",
            "owner_profile_id": owner, "opening_balance": 0, "opening_date": today,
        })
        post(client, "transactions", {
            "account_id": account["id"], "date": today, "amount": -amount,
            "payee": "Synthetic shop", "category_id": category["id"],
        })
        with Session(client.app.state.engine) as session:
            session.add(ExpectedMovement(
                household_id=int(client.headers["X-Household-Id"]), account_id=account["id"],
                expected_date=date.today(), amount_pence=-amount, label="Synthetic bill",
                confidence=Confidence.scheduled, source_type=ExpectedSource.subscription,
                source_id=account["id"],
            ))
            session.commit()
        if owner is None:
            continue
        day = today if owner == admin else (date.today() - timedelta(days=40)).isoformat()
        budget = post(client, "budgets", {
            "name": f"Synthetic plan {owner}", "owner_profile_id": owner,
            "period_type": "custom", "start_date": day, "end_date": day,
        })
        post(client, f"budgets/{budget['id']}/lines", {
            "category_id": category["id"], "side": "expense", "amount": 10000,
        })
    response = client.get(f"/api/v1/budgets/reports/combined?start={today}&end={today}")
    assert response.status_code == 200, response.text
    combined = response.json()
    assert combined["detail"]["lines"][0]["effective_pence"] == 10000
    assert combined["actuals"]["spent_pence"] == 7000
    assert sum(combined["actuals"]["totals"].values()) == 7000
    assert combined["actuals"]["bills_remaining_pence"] == 7000
    assert combined["actuals"]["bills_remaining_count"] == 3
