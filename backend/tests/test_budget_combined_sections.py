"""All sections keep authorized calendar actuals even when a plan is missing."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain.budget_periods import set_day
from app.main import create_app
from app.persistence.models import (
    Account,
    Budget,
    BudgetLine,
    Category,
    Household,
    Transaction,
    UserProfile,
)

START, END = date(2025, 11, 1), date(2025, 11, 30)


@pytest.fixture()
def setup(tmp_path):
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"})
    with TestClient(create_app(settings)) as client:
        with Session(client.app.state.engine) as session:
            household = Household(name="Synthetic")
            session.add(household)
            session.flush()
            profiles = {}
            for role in ["admin", "member", "other", "viewer"]:
                profile = UserProfile(household_id=household.id, display_name=role.title(),
                                      role="member" if role == "other" else role)
                session.add(profile)
                session.flush()
                profiles[role] = profile.id
            category = Category(household_id=household.id, name="Synthetic expense")
            session.add(category)
            session.flush()
            for index, owner in enumerate([None, profiles["member"], profiles["other"]]):
                account = Account(household_id=household.id, owner_profile_id=owner,
                                  name=f"Synthetic {index}", type="personal", opening_date=START)
                session.add(account)
                session.flush()
                session.add_all([
                    Transaction(account_id=account.id, date=date(2025, 10, 29),
                                amount_pence=-90, payee="Before calendar", category_id=category.id),
                    Transaction(account_id=account.id, date=date(2025, 11, 29),
                                amount_pence=-(index + 1) * 100, payee="Calendar expense",
                                category_id=category.id),
                ])
                if index < 2:
                    budget = Budget(household_id=household.id, owner_profile_id=owner,
                        name=f"Synthetic plan {index}", period_type="monthly", start_date=START,
                        end_date=END, status="locked")
                    session.add(budget)
                    session.flush()
                    session.add(BudgetLine(budget_id=budget.id, category_id=category.id,
                        side="expense", original_pence=index * 1000, rollover=False))
                else:
                    session.add(Transaction(account_id=account.id, date=date(2025, 11, 5),
                                            amount_pence=700, payee="Unplanned receipt"))
            set_day(session, household.id, profiles["member"], 28)
            session.commit()
            household_id = household.id
        client.headers["X-Household-Id"] = str(household_id)
        yield client, profiles


@pytest.mark.parametrize("role,owners,spent", [
    ("admin", [None, "member", "other"], 600),
    ("member", [None, "member"], 300),
    ("viewer", [None], 100),
])
def test_sections_are_visible_once_and_use_calendar_actuals(setup, role, owners, spent):
    client, profiles = setup
    client.headers["X-Profile-Id"] = str(profiles[role])
    response = client.get("/api/v1/budgets/reports/combined", params={"start": START, "end": END})
    assert response.status_code == 200, response.text
    result = response.json()
    sections = result["sections"]
    assert [s["owner_profile_id"] for s in sections] == [
        profiles[owner] if owner else None for owner in owners]
    assert sections[0]["name"] == "Shared"
    assert result["actuals"]["spent_pence"] == spent
    assert sum(s["actuals"]["spent_pence"] for s in sections) == spent
    for section in sections:
        assert section["actuals"]["period"] == {
            "start": START.isoformat(), "end": END.isoformat(), "basis": "calendar",
            "employment_id": None, "warning": None}
        if section["detail"]:
            assert section["detail"]["read_only"] is True
    if role == "admin":
        by_owner = {s["owner_profile_id"]: s for s in sections}
        assert by_owner[None]["detail"]["lines"][0]["effective_pence"] == 0
        missing = by_owner[profiles["other"]]
        assert missing["name"] == "Other" and missing["detail"] is None
        assert missing["actuals"]["spent_pence"] == 300
        assert missing["actuals"]["income_received_pence"] == 700


def test_combined_section_get_never_rewrites_history(setup):
    client, profiles = setup
    client.headers["X-Profile-Id"] = str(profiles["admin"])
    writes = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    engine = client.app.state.engine
    event.listen(engine, "before_cursor_execute", observe)
    try:
        result = client.get("/api/v1/budgets/reports/combined", params={"start": START, "end": END})
        assert result.status_code == 200, result.text
        assert len(result.json()["sections"]) == 3
        assert writes == []
    finally:
        event.remove(engine, "before_cursor_execute", observe)


def test_shared_section_remains_explicit_without_a_plan_or_accounts(setup):
    client, profiles = setup
    with Session(client.app.state.engine) as session:
        for row in session.scalars(select(Account).where(Account.owner_profile_id.is_(None))):
            row.owner_profile_id = profiles["member"]
        for row in session.scalars(select(Budget).where(Budget.owner_profile_id.is_(None))):
            row.owner_profile_id = profiles["member"]
        session.commit()
    client.headers["X-Profile-Id"] = str(profiles["member"])
    result = client.get("/api/v1/budgets/reports/combined", params={"start": START, "end": END})
    assert result.status_code == 200, result.text
    shared = result.json()["sections"][0]
    assert shared["owner_profile_id"] is None and shared["detail"] is None
    assert shared["actuals"]["spent_pence"] == 0
