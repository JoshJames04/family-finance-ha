"""Review probes: copy into backend/tests to use its migrated DB fixtures.

Each test asserts the intended behaviour and should fail on 85f3a3e.
Uses synthetic data only; no running service or container access.
"""

import json
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.scheduler import sync_tick
from app.core.settings import Settings
from app.domain import contributions, settle
from app.domain.discovery import DiscoveredAccount
from app.main import create_app
from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    BankProvider,
    Confidence,
    ConnStatus,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    MappingStatus,
    RecurringCommitment,
    Transaction,
    TransactionSource,
    TransactionStatus,
)


def account_for(session, household):
    account = Account(
        household_id=household.id,
        name="Review current",
        type="personal",
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    session.add(account)
    session.flush()
    return account


def test_scheduler_releases_writer_before_balance_http(session, household):
    account = account_for(session, household)
    connection = BankConnection(
        household_id=household.id,
        provider=BankProvider.starling,
        vault_ref="synthetic-review-ref",
        status=ConnStatus.connected,
    )
    session.add(connection)
    session.flush()
    session.add(
        BankAccountMapping(
            household_id=household.id,
            connection_id=connection.id,
            account_id=account.id,
            external_uid="review-uid",
            external_label="Old provider label",
            external_category_uid="review-category",
            status=MappingStatus.linked,
        )
    )
    session.commit()
    database = session.get_bind().url.database
    probes = []

    class Client:
        def accounts(self, token):
            # Rename guarantees discover has a real write to flush.
            return [
                DiscoveredAccount(
                    "review-uid", "New provider label", category_uid="review-category"
                )
            ]

        def balance(self, token, external_uid, category_uid=None):
            with sqlite3.connect(database, timeout=0.05) as other:
                try:
                    other.execute("BEGIN IMMEDIATE")
                    other.execute("UPDATE household SET name=name")
                    other.commit()
                    probes.append("writer available")
                except sqlite3.OperationalError as exc:
                    probes.append(str(exc))
            return 10000

        def feed_items(self, token, external_uid, category_uid, since):
            return []

    sync_tick(session, client=Client(), token_for=lambda *_: "synthetic-token")
    assert probes == ["writer available"], probes


@pytest.fixture()
def review_client(tmp_path):
    with TestClient(
        create_app(Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"}))
    ) as client:
        response = client.post("/api/v1/households", json={"name": "Review"})
        assert response.status_code == 201, response.text
        client.headers.update({"X-Household-Id": str(response.json()["id"])})
        yield client


def bill(client, name):
    response = client.post(
        "/api/v1/accounts",
        json={
            "name": "Review",
            "type": "personal",
            "opening_balance": 0,
            "opening_date": "2025-01-01",
        },
    )
    assert response.status_code == 201, response.text
    response = client.post(
        "/api/v1/commitments",
        json={
            "name": name,
            "account_id": response.json()["id"],
            "amount": -1000,
            "frequency": "monthly",
            "next_date": "2027-01-15",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def subscription(client):
    response = client.post(
        "/api/v1/subscriptions",
        json={
            "payee": "Netflix",
            "frequency": "monthly",
            "expected": 1000,
            "next_renewal_date": "2027-01-15",
        },
    )
    assert response.status_code == 201, response.text


def test_commitment_rename_rejects_active_subscription(review_client):
    client = review_client
    cid = bill(client, "Other")
    subscription(client)
    response = client.patch(f"/api/v1/commitments/{cid}", json={"name": "Netflix"})
    assert response.status_code == 409, response.text


def test_commitment_activation_rejects_active_subscription(review_client):
    client = review_client
    cid = bill(client, "Netflix")
    response = client.delete(f"/api/v1/commitments/{cid}")
    assert response.status_code == 204, response.text
    subscription(client)
    response = client.post(f"/api/v1/commitments/{cid}/activate")
    assert response.status_code == 409, response.text


def test_partial_settlement_does_not_advance_unpaid_head(session, household):
    account = account_for(session, household)
    due = date(2025, 1, 15)
    commitment = RecurringCommitment(
        household_id=household.id,
        account_id=account.id,
        name="Rent",
        amount_pence=-10000,
        frequency=Frequency.monthly,
        next_date=due,
    )
    session.add(commitment)
    session.flush()
    movement = ExpectedMovement(
        household_id=household.id,
        account_id=account.id,
        expected_date=due,
        amount_pence=-10000,
        label="Rent",
        confidence=Confidence.scheduled,
        source_type=ExpectedSource.commitment,
        source_id=commitment.id,
    )
    transaction = Transaction(
        account_id=account.id,
        date=due,
        amount_pence=-5000,
        payee="Rent",
        status=TransactionStatus.cleared,
        source=TransactionSource.starling,
    )
    session.add_all([movement, transaction])
    session.flush()
    match = settle.settle_occurrence(session, movement, transaction, manual=True)
    assert match is not None
    assert match.matched_pence == 5000
    assert contributions.status(session, movement).outstanding_pence == 5000
    assert commitment.next_date == due, (
        "Bill head advanced despite a half-unpaid occurrence",
        commitment.next_date,
    )


def test_expected_movements_hide_another_members_private_account(review_client):
    client = review_client
    owner = client.post("/api/v1/profiles", json={"display_name": "Owner", "role": "admin"}).json()
    member = client.post(
        "/api/v1/profiles", json={"display_name": "Other", "role": "member"}
    ).json()
    response = client.post(
        "/api/v1/accounts",
        json={
            "name": "Private",
            "type": "personal",
            "opening_balance": 0,
            "opening_date": "2025-01-01",
            "owner_profile_id": owner["id"],
        },
        headers={"X-Profile-Id": str(owner["id"])},
    )
    assert response.status_code == 201, response.text
    aid = response.json()["id"]
    day = date.today().isoformat()
    response = client.post(
        "/api/v1/expected-movements",
        json={
            "account_id": aid,
            "expected_date": day,
            "amount": 12345,
            "label": "Synthetic private salary",
        },
        headers={"X-Profile-Id": str(owner["id"])},
    )
    assert response.status_code == 201, response.text
    response = client.get(
        f"/api/v1/expected-movements?start={day}&end={day}",
        headers={"X-Profile-Id": str(member["id"])},
    )
    assert response.status_code == 200
    assert not any(m["account_id"] == aid for m in response.json()), (
        "Private expected movement disclosed to another member"
    )


def test_adding_salary_revision_creates_missing_employment_projections(review_client):
    client = review_client
    owner = client.post("/api/v1/profiles", json={"display_name": "Owner", "role": "admin"}).json()
    acct = client.post(
        "/api/v1/accounts",
        json={
            "name": "Salary",
            "type": "personal",
            "opening_balance": 0,
            "opening_date": "2025-01-01",
        },
    ).json()
    today = date.today()
    end = today + timedelta(days=60)
    response = client.post(
        "/api/v1/employments",
        json={
            "profile_id": owner["id"],
            "employer": "Synthetic",
            "jurisdiction": "england_wales",
            "contracted_hours": "37.5",
            "start_date": today.isoformat(),
            "paid_into_account_id": acct["id"],
            "deduction_mode": "gross_only",
            "payday_rule": json.dumps({"kind": "fixed_day", "day": 28, "adjust": "none"}),
        },
    )
    assert response.status_code == 201, response.text
    eid = response.json()["id"]
    response = client.post(
        f"/api/v1/employments/{eid}/salary-revisions",
        json={"effective_date": today.isoformat(), "annual_salary": 3600000},
    )
    assert response.status_code == 201, response.text
    moves = client.get(f"/api/v1/expected-movements?start={today}&end={end}").json()
    assert any(m["source_type"] == "employment" and m["source_id"] == eid for m in moves), (
        "Valid employment remains without paydays after salary revision"
    )


def test_runtime_diagnostics_redacts_sensitive_error_digits(review_client):
    review_client.app.state.telemetry.record_tick(1, "Synthetic payroll amount 123456789")
    result = review_client.get("/api/v1/diagnostics")
    assert result.status_code == 200
    assert "123456789" not in result.text, "Runtime diagnostics leaks unredacted exception text"


@pytest.mark.parametrize("with_splits", [False, True])
def test_personal_budget_actuals_only_owner(session, household, with_splits):
    from app.domain import budgets, splits
    from app.persistence.models import (
        Budget,
        BudgetLine,
        BudgetLineSide,
        BudgetPeriodType,
        Category,
        Role,
        UserProfile,
    )

    owner = UserProfile(household_id=household.id, display_name="Owner", role=Role.member)
    other = UserProfile(household_id=household.id, display_name="Other", role=Role.member)
    session.add_all([owner, other])
    session.flush()
    category = Category(household_id=household.id, name="Food")
    second = Category(household_id=household.id, name="Other")
    session.add_all([category, second])
    session.flush()
    budget = Budget(
        household_id=household.id,
        owner_profile_id=owner.id,
        name="Private",
        period_type=BudgetPeriodType.monthly,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
    )
    session.add(budget)
    session.flush()
    line = BudgetLine(
        budget_id=budget.id,
        category_id=category.id,
        side=BudgetLineSide.expense,
        original_pence=10000,
    )
    session.add(line)
    for profile, amount in [(owner, 1000), (other, 9000)]:
        account = Account(
            household_id=household.id,
            name=profile.display_name,
            type="personal",
            opening_balance_pence=0,
            opening_date=date(2025, 1, 1),
            owner_profile_id=profile.id,
        )
        session.add(account)
        session.flush()
        tx = Transaction(
            account_id=account.id,
            date=date(2025, 1, 15),
            amount_pence=-amount,
            payee="Food",
            category_id=category.id,
            status=TransactionStatus.cleared,
            source=TransactionSource.manual,
        )
        session.add(tx)
        session.flush()
        if with_splits:
            splits.replace(
                session,
                tx,
                household.id,
                [
                    {"category_id": category.id, "amount_pence": -amount // 2},
                    {"category_id": second.id, "amount_pence": -amount // 2},
                ],
            )
    session.flush()
    assert budgets.actuals_for_budget(session, budget)[line.id] == (500 if with_splits else 1000)
    budget.owner_profile_id = None
    assert budgets.actuals_for_budget(session, budget)[line.id] == (5000 if with_splits else 10000)


def test_subscription_rename_rejects_active_bill(review_client):
    client = review_client
    bill(client, "Netflix")
    response = client.post(
        "/api/v1/subscriptions",
        json={
            "payee": "Other",
            "frequency": "monthly",
            "expected": 1000,
            "next_renewal_date": "2027-01-15",
        },
    )
    assert response.status_code == 201, response.text
    response = client.patch(
        f"/api/v1/subscriptions/{response.json()['id']}", json={"payee": "Netflix"}
    )
    assert response.status_code == 409, response.text


def test_historical_category_replay_ignores_recent_watermark(session, household):
    from app.domain import sync
    from app.integrations.starling import starling_fetch

    account = account_for(session, household)
    tx = Transaction(
        account_id=account.id,
        date=date(2020, 1, 1),
        amount_pence=-123,
        payee="Historical",
        source=TransactionSource.starling,
        external_ref="old-ref",
        status=TransactionStatus.cleared,
    )
    session.add(tx)
    session.flush()
    cursor = sync.cursor_for(session, account.id, TransactionSource.starling)
    cursor.last_ref = "2026-11-01T00:00:00.000Z"
    seen = []

    class Client:
        def feed_items(self, token, uid, category, since):
            seen.append(since)
            return [
                sync.FeedItem(
                    "old-ref", date(2020, 1, 1), -123, "Historical", provider_category="GROCERIES"
                )
            ]

    sync.sync_account(
        session,
        account,
        TransactionSource.starling,
        starling_fetch(Client(), "synthetic", "uid", "cat", historical_since=date(2020, 1, 1)),
    )
    assert seen == ["2020-01-01T00:00:00.000Z"]
    assert tx.provider_category == "GROCERIES"
    from sqlalchemy import func, select

    assert session.scalar(select(func.count(Transaction.id))) == 1


def test_shared_accountless_manual_plan_visible(review_client):
    client = review_client
    day = date.today().isoformat()
    response = client.post(
        "/api/v1/expected-movements",
        json={"expected_date": day, "amount": 123, "label": "Shared plan"},
    )
    assert response.status_code == 201, response.text
    rows = client.get(f"/api/v1/expected-movements?start={day}&end={day}").json()
    assert any(row["label"] == "Shared plan" for row in rows)


def test_private_outstanding_not_disclosed(review_client):
    client = review_client
    owner = client.post("/api/v1/profiles", json={"display_name": "Owner", "role": "admin"}).json()
    member = client.post(
        "/api/v1/profiles", json={"display_name": "Member", "role": "member"}
    ).json()
    account = client.post(
        "/api/v1/accounts",
        json={
            "name": "Private",
            "type": "personal",
            "opening_balance": 0,
            "opening_date": "2025-01-01",
            "owner_profile_id": owner["id"],
        },
    ).json()
    response = client.post(
        "/api/v1/expected-movements",
        json={
            "account_id": account["id"],
            "expected_date": "2025-01-15",
            "amount": -1000,
            "label": "Private debt",
        },
    )
    assert response.status_code == 201, response.text
    rows = client.get(
        "/api/v1/expected-movements/outstanding", headers={"X-Profile-Id": str(member["id"])}
    ).json()
    assert not any(row["label"] == "Private debt" for row in rows)


def test_balance_http_all_fetched_before_reconciliation_writes(session, household):
    from app.domain import discovery

    account = account_for(session, household)
    account.opening_date = date(2025, 2, 1)
    session.add(
        Transaction(
            account_id=account.id,
            date=date(2025, 1, 1),
            amount_pence=1,
            payee="Old history",
            source=TransactionSource.manual,
            status=TransactionStatus.cleared,
        )
    )
    conn = BankConnection(
        household_id=household.id,
        provider=BankProvider.starling,
        vault_ref="synthetic",
        status=ConnStatus.connected,
    )
    session.add(conn)
    session.flush()
    for uid in ["one", "two"]:
        session.add(
            BankAccountMapping(
                household_id=household.id,
                connection_id=conn.id,
                account_id=account.id,
                external_uid=uid,
                external_label=uid,
                external_category_uid=uid,
                status=MappingStatus.linked,
            )
        )
    database = session.get_bind().url.database
    probes = []

    class Client:
        def balance(self, *args):
            with sqlite3.connect(database, timeout=0.05) as other:
                try:
                    other.execute("BEGIN IMMEDIATE")
                    other.execute("UPDATE household SET name=name")
                    other.commit()
                    probes.append("available")
                except sqlite3.OperationalError as exc:
                    probes.append(str(exc))
            return 1000

    assert discovery.sync_balances(session, conn, Client(), "synthetic") == 2
    assert probes == ["available", "available"]


def test_expected_selected_budget_filters_other_owner(review_client):
    client = review_client
    owner = client.post("/api/v1/profiles", json={"display_name": "Owner", "role": "admin"}).json()
    other = client.post("/api/v1/profiles", json={"display_name": "Other", "role": "member"}).json()
    day = date.today().isoformat()
    for profile in [owner, other]:
        account = client.post(
            "/api/v1/accounts",
            json={
                "name": profile["display_name"],
                "type": "personal",
                "opening_balance": 0,
                "opening_date": "2025-01-01",
                "owner_profile_id": profile["id"],
            },
        ).json()
        response = client.post(
            "/api/v1/expected-movements",
            json={
                "account_id": account["id"],
                "expected_date": day,
                "amount": 100,
                "label": profile["display_name"],
            },
        )
        assert response.status_code == 201, response.text
    response = client.post(
        "/api/v1/budgets",
        json={
            "name": "Mine",
            "period_type": "monthly",
            "start_date": day,
            "end_date": day,
            "owner_profile_id": owner["id"],
        },
    )
    assert response.status_code == 201, response.text
    budget_id = response.json()["id"]
    rows = client.get(
        f"/api/v1/expected-movements?start={day}&end={day}&budget_id={budget_id}",
        headers={"X-Profile-Id": str(owner["id"])},
    ).json()
    assert [row["label"] for row in rows] == ["Owner"]


def test_subscription_partial_payment_keeps_head_and_records_total_on_completion(
    session, household
):
    from app.persistence.models import Subscription

    account = account_for(session, household)
    due = date(2025, 1, 15)
    sub = Subscription(
        household_id=household.id,
        payee="Synthetic",
        payee_normalised="synthetic",
        frequency=Frequency.monthly,
        expected_pence=10000,
        next_renewal_date=due,
        anchor_day=15,
        account_id=account.id,
    )
    session.add(sub)
    session.flush()
    movement = ExpectedMovement(
        household_id=household.id,
        account_id=account.id,
        expected_date=due,
        amount_pence=-10000,
        label="Synthetic",
        confidence=Confidence.scheduled,
        source_type=ExpectedSource.subscription,
        source_id=sub.id,
    )
    session.add(movement)
    session.flush()
    for index in range(2):
        txn = Transaction(
            account_id=account.id,
            date=due,
            amount_pence=-5000,
            payee="Synthetic",
            source=TransactionSource.manual,
            status=TransactionStatus.cleared,
        )
        session.add(txn)
        session.flush()
        assert settle.settle_occurrence(session, movement, txn, manual=True) is not None
        if index == 0:
            assert sub.next_renewal_date == due
            assert len(sub.prices) == 0
    assert sub.next_renewal_date == date(2025, 2, 15)
    session.expire(sub, ["prices"])
    assert len(sub.prices) == 1
    assert sub.prices[0].amount_pence == 10000


@pytest.mark.parametrize("action", ["delete", "write-off", "reschedule", "matches"])
def test_member_cannot_mutate_another_private_movement(review_client, action):
    client = review_client
    owner = client.post("/api/v1/profiles", json={"display_name": "Owner", "role": "admin"}).json()
    member = client.post(
        "/api/v1/profiles", json={"display_name": "Member", "role": "member"}
    ).json()
    account = client.post(
        "/api/v1/accounts",
        json={
            "name": "Private",
            "type": "personal",
            "opening_balance": 0,
            "opening_date": "2025-01-01",
            "owner_profile_id": owner["id"],
        },
    ).json()
    movement = client.post(
        "/api/v1/expected-movements",
        json={
            "account_id": account["id"],
            "expected_date": "2025-01-15",
            "amount": -1000,
            "label": "Private",
        },
    ).json()
    txn = client.post(
        "/api/v1/transactions",
        headers={"X-Profile-Id": str(owner["id"])},
        json={
            "account_id": account["id"],
            "date": "2025-01-15",
            "amount": -1000,
            "payee": "Private",
        },
    )
    assert txn.status_code == 201, txn.text
    headers = {"X-Profile-Id": str(member["id"])}
    url = f"/api/v1/expected-movements/{movement['id']}"
    if action == "delete":
        response = client.delete(url, headers=headers)
    else:
        body = (
            {"due_date": "2027-01-01"}
            if action == "reschedule"
            else {"transaction_id": txn.json()["id"]}
            if action == "matches"
            else {}
        )
        response = client.post(f"{url}/{action}", headers=headers, json=body)
    assert response.status_code == 404, response.text


def test_historical_replay_requires_admin_before_lookup(review_client):
    client = review_client
    response = client.post("/api/v1/banking/connections/999/sync?historical_since=2020-01-01")
    assert response.status_code == 403, response.text
    owner = client.post("/api/v1/profiles", json={"display_name": "Owner", "role": "admin"}).json()
    response = client.post(
        "/api/v1/banking/connections/999/sync?historical_since=2099-01-01",
        headers={"X-Profile-Id": str(owner["id"])},
    )
    assert response.status_code == 422, response.text

