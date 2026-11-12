"""Global financial search uses authorised transactions and literal text."""
from datetime import date

from fastapi.testclient import TestClient

from app.main import create_app
from app.persistence.models import Account, AccountType, Category, Transaction
from app.persistence.session import get_session


def test_search_notes_categories_and_signed_pounds(session, household):
    account = Account(household_id=household.id, name="Shared", type=AccountType.joint,
                      currency="GBP", opening_balance_pence=0, opening_date=date(2026, 1, 1))
    category = Category(household_id=household.id, name="Groceries")
    session.add_all([account, category])
    session.flush()
    txn = Transaction(account_id=account.id, date=date(2026, 1, 2), amount_pence=-1234,
                      payee="Shop", notes="birthday supplies", category_id=category.id)
    session.add(txn)
    session.flush()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)
    for query in ("birthday", "Groceries", "-£12.34", "12.34", "Shop"):
        result = client.get("/api/v1/transactions", params={"q": query})
        assert result.status_code == 200
        assert [item["id"] for item in result.json()["items"]] == [txn.id], query
    assert client.get("/api/v1/transactions", params={"q": "%"}).json()["total"] == 0
