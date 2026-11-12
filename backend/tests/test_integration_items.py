"""Per-item discovery respects shared visibility and stable source identity."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.persistence.models import Account, AccountType, ExpectedMovement, ExpectedSource
from app.persistence.session import get_session


def test_item_bill_identity_survives_new_occurrence(session, household):
    account = Account(household_id=household.id, name="Shared", type=AccountType.joint,
                      currency="GBP", opening_balance_pence=0, opening_date=date(2026, 1, 1))
    session.add(account)
    session.flush()
    for offset in (2, 32):
        session.add(ExpectedMovement(household_id=household.id, account_id=account.id,
                    source_type=ExpectedSource.commitment, source_id=7,
                    expected_date=date.today() + timedelta(days=offset),
                    amount_pence=-1234, label="Synthetic bill", confidence="scheduled"))
    session.flush()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    response = TestClient(app).get("/api/v1/integration/items")
    assert response.status_code == 200
    items = response.json()["items"]
    bills = [item for item in items if item["family"] == "bills"]
    assert len(bills) == 1
    assert bills[0]["id"] == "commitment:7"
    assert bills[0]["value"] == 2
    assert bills[0]["unit"] == "d"
