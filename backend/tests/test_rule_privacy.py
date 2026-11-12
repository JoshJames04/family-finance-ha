"""Member-facing categorisation automation cannot expose or rewrite private accounts."""

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import (
    Account,
    CategorisationRule,
    Category,
    Household,
    Transaction,
    UserProfile,
)


def test_rule_visibility_authoring_and_apply_follow_account_scope(tmp_path):
    app = create_app(
        Settings.load(
            {
                "FF_DATA_DIR": str(tmp_path),
                "FF_SCHEDULER_TICK_SECONDS": "0",
                "FF_BACKUP_INTERVAL_HOURS": "0",
            }
        )
    )
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            household = Household(name="Synthetic")
            session.add(household)
            session.flush()
            members = [
                UserProfile(household_id=household.id, display_name=name, role="member")
                for name in ("A", "B")
            ]
            session.add_all(members)
            session.flush()
            accounts = [
                Account(
                    household_id=household.id,
                    name=name,
                    type="personal",
                    owner_profile_id=owner.id,
                    opening_balance_pence=0,
                    opening_date=date(2025, 1, 1),
                )
                for name, owner in zip(("A account", "B account"), members, strict=False)
            ]
            category = Category(household_id=household.id, name="Synthetic category")
            session.add_all([*accounts, category])
            session.flush()
            private_rule = CategorisationRule(
                household_id=household.id,
                pattern="Private counterparty",
                category_id=category.id,
                account_id=accounts[1].id,
                amount_pence=-99,
                source="manual",
                auto_apply=True,
            )
            session.add(private_rule)
            transactions = [
                Transaction(
                    account_id=account.id,
                    date=date(2025, 4, 10),
                    amount_pence=-99,
                    payee="Merchant",
                )
                for account in accounts
            ]
            transactions[1].provider_category = "GENERAL"
            session.add(
                CategorisationRule(
                    household_id=household.id,
                    pattern="Legacy private",
                    category_id=category.id,
                    source="learned",
                )
            )
            session.add(
                Transaction(
                    account_id=accounts[1].id,
                    date=date(2025, 4, 10),
                    amount_pence=-8,
                    payee="Legacy private",
                )
            )
            session.add_all(transactions)
            session.flush()
            headers = {"X-Profile-Id": str(members[0].id)}
            aid, bid, cid, rid = accounts[0].id, accounts[1].id, category.id, private_rule.id
            own_tid, other_tid = [t.id for t in transactions]
            session.commit()
        assert client.get("/api/v1/categorisation/rules", headers=headers).json() == []
        assert (
            client.patch(
                f"/api/v1/categorisation/rules/{rid}", headers=headers, json={"auto_apply": False}
            ).status_code
            == 404
        )
        assert (
            client.delete(f"/api/v1/categorisation/rules/{rid}", headers=headers).status_code == 404
        )
        for scope in [{"account_id": bid}, {"account_id": aid, "transfer_account_id": bid}]:
            response = client.post(
                "/api/v1/categorisation/rules",
                headers=headers,
                json={"pattern": "Merchant", "match_type": "exact", "category_id": cid, **scope},
            )
            assert response.status_code in {403, 404, 422}
        assert (
            client.post(
                "/api/v1/categorisation/rules",
                headers=headers,
                json={"pattern": "Merchant", "category_id": cid, "auto_apply": True},
            ).status_code
            == 201
        )
        assert client.post("/api/v1/categorisation/apply", headers=headers).status_code == 200
        assert (
            client.get("/api/v1/categorisation/provider-categories", headers=headers).json() == []
        )
        assert (
            client.put(
                "/api/v1/categorisation/provider-categories",
                headers=headers,
                json=[{"provider": "manual", "provider_category": "GENERAL", "category_id": cid}],
            ).status_code
            == 200
        )
        assert (
            client.post("/api/v1/categorisation/auto-pair", headers=headers).json()["linked"] == 0
        )
        assert (
            client.post("/api/v1/categorisation/auto-refund", headers=headers).json()["linked"] == 0
        )
        with Session(app.state.engine) as session:
            assert session.get(Transaction, own_tid).category_id == cid
            assert session.get(Transaction, other_tid).category_id is None
