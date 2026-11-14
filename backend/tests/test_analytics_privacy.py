"""Real analytics routes respect account and loan privacy for every role."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import (
    Account,
    Category,
    Frequency,
    Household,
    Loan,
    LoanDirection,
    RecurringCommitment,
    Role,
    Transaction,
    TransactionStatus,
    UserProfile,
)


@pytest.fixture
def analytics_client(tmp_path):
    settings = Settings.load(
        {
            "FF_DATA_DIR": str(tmp_path),
            "FF_SCHEDULER_TICK_SECONDS": "0",
            "FF_ADMIN_TOKEN": "synthetic-analytics-admin-token",
        }
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            household = Household(name="Synthetic analytics")
            session.add(household)
            session.flush()
            profiles = {}
            for name, role in [
                ("admin", Role.admin),
                ("member", Role.member),
                ("other", Role.member),
                ("viewer", Role.viewer),
            ]:
                profile = UserProfile(household_id=household.id, display_name=name, role=role)
                session.add(profile)
                session.flush()
                profiles[name] = profile.id
            anchor = date.today().replace(day=1)
            previous = (anchor - timedelta(days=1)).replace(day=15)
            categories = {}
            for index, owner in enumerate(
                [None, profiles["member"], profiles["other"], profiles["viewer"]]
            ):
                account = Account(
                    household_id=household.id,
                    owner_profile_id=owner,
                    name=f"Account {index}",
                    type="personal",
                    opening_date=previous,
                    opening_balance_pence=0,
                )
                category = Category(household_id=household.id, name=f"Category {index}")
                session.add_all([account, category])
                session.flush()
                categories[index] = category.id
                session.add_all(
                    [
                        Transaction(
                            account_id=account.id,
                            date=previous,
                            amount_pence=-(100 * 2**index),
                            payee="Historical expense",
                            category_id=category.id,
                            status=TransactionStatus.cleared,
                        ),
                        Transaction(
                            account_id=account.id,
                            date=anchor,
                            amount_pence=1000 * 2**index,
                            payee="Current income",
                            status=TransactionStatus.cleared,
                        ),
                        Loan(
                            household_id=household.id,
                            owner_profile_id=owner,
                            party=f"Loan {index}",
                            direction=LoanDirection.we_owe,
                            principal_pence=10 * 2**index,
                            start_date=previous,
                            interest_bps_pa=0,
                        ),
                        RecurringCommitment(
                            household_id=household.id,
                            account_id=account.id,
                            category_id=category.id,
                            name=f"Commitment {index}",
                            amount_pence=-(500 + 100 * index),
                            frequency=Frequency.monthly,
                            next_date=anchor,
                        ),
                    ]
                )
            session.commit()
        yield client, profiles, categories, app


@pytest.mark.parametrize(
    "role,income,expense,networth,visible",
    [
        ("admin", 15000, 1500, 13350, [0, 1, 2, 3]),
        ("member", 3000, 300, 2670, [0, 1]),
        ("viewer", 1000, 100, 890, [0]),
        ("service", 1000, 100, 890, [0]),
    ],
)
def test_analytics_reads_are_rbac_scoped(
    analytics_client, role, income, expense, networth, visible
):
    client, profiles, categories, _ = analytics_client
    headers = (
        {"X-Admin-Token": "synthetic-analytics-admin-token"}
        if role == "service"
        else {"X-Profile-Id": str(profiles[role])}
    )
    monthly = client.get("/api/v1/analytics/monthly?months=2", headers=headers)
    assert monthly.status_code == 200, monthly.text
    rows = monthly.json()
    assert rows[-1]["income_pence"] == income
    assert rows[-2]["expense_pence"] == expense
    assert rows[-1]["net_worth_pence"] == networth
    current = client.get("/api/v1/analytics/net-worth", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["net_worth_pence"] == networth
    forecast = client.get("/api/v1/analytics/forecast?months=1&history_months=1", headers=headers)
    assert forecast.status_code == 200, forecast.text
    assert {row["category_id"] for row in forecast.json()} == {
        categories[index] for index in visible
    }
    assert sum(row["amount_pence"] for row in forecast.json()) == sum(
        500 + 100 * index for index in visible
    )
    rebuild = client.post("/api/v1/analytics/rebuild", headers=headers)
    assert rebuild.status_code == (200 if role in {"admin", "service"} else 403), rebuild.text


def test_ingress_service_analytics_remains_outside_service_allowlist(analytics_client):
    client, _, _, app = analytics_client
    app.state.settings.ingress = True
    for route in ["monthly", "net-worth", "forecast"]:
        response = client.get(
            f"/api/v1/analytics/{route}",
            headers={"X-Admin-Token": "synthetic-analytics-admin-token"},
        )
        assert response.status_code == 403


def test_empty_authorized_scope_does_not_fall_back_to_household(analytics_client):
    client, profiles, _, app = analytics_client
    with Session(app.state.engine) as session:
        shared = session.scalar(select(Account).where(Account.owner_profile_id.is_(None)))
        shared.owner_profile_id = profiles["other"]
        shared_loan = session.scalar(select(Loan).where(Loan.owner_profile_id.is_(None)))
        shared_loan.owner_profile_id = profiles["other"]
        session.commit()
    headers = {"X-Profile-Id": str(profiles["viewer"])}
    assert client.get("/api/v1/analytics/monthly", headers=headers).json() == []
    assert client.get("/api/v1/analytics/net-worth", headers=headers).json() == {
        "net_worth_pence": 0
    }
    assert client.get("/api/v1/analytics/forecast", headers=headers).json() == []


def test_rebuild_does_not_accept_an_admin_from_another_household(analytics_client):
    client, profiles, _, app = analytics_client
    with Session(app.state.engine) as session:
        other = Household(name="Other synthetic household")
        session.add(other)
        session.commit()
        other_id = other.id
    response = client.post("/api/v1/analytics/rebuild", headers={
        "X-Profile-Id": str(profiles["admin"]), "X-Household-Id": str(other_id),
    })
    assert response.status_code == 404
