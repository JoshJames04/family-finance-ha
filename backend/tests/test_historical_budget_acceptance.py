"""Independent historical acceptance probes. Synthetic disposable DBs only."""

import calendar
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.domain import budgets, contributions, reports, settle
from app.main import create_app
from app.persistence.models import (
    Account,
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetPeriodType,
    Category,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    RecurringCommitment,
    Role,
    Transaction,
    TransactionSource,
    TransactionStatus,
    UserProfile,
)


def fixture(session, household):
    owner = UserProfile(household_id=household.id, display_name="Synthetic A", role=Role.member)
    other = UserProfile(household_id=household.id, display_name="Synthetic B", role=Role.member)
    session.add_all([owner, other])
    session.flush()
    a = Account(
        household_id=household.id,
        name="Synthetic account A",
        type="personal",
        opening_date=date(2025, 1, 1),
        owner_profile_id=owner.id,
    )
    b = Account(
        household_id=household.id,
        name="Synthetic account B",
        type="personal",
        opening_date=date(2025, 1, 1),
        owner_profile_id=other.id,
    )
    c = Category(household_id=household.id, name="Synthetic planned")
    other_cat = Category(household_id=household.id, name="Synthetic unplanned")
    session.add_all([a, b, c, other_cat])
    session.flush()
    today = date.today()
    start = today.replace(day=1)
    end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    budget = Budget(
        household_id=household.id,
        owner_profile_id=owner.id,
        name="Synthetic personal",
        period_type=BudgetPeriodType.monthly,
        start_date=start,
        end_date=end,
    )
    session.add(budget)
    session.flush()
    line = BudgetLine(
        budget_id=budget.id, category_id=c.id, side=BudgetLineSide.expense, original_pence=10000
    )
    session.add(line)
    session.flush()
    return a, b, c, other_cat, budget, line


def tx(session, account, category, amount, day=None):
    row = Transaction(
        account_id=account.id,
        date=day or date.today(),
        amount_pence=amount,
        payee="Synthetic",
        category_id=category.id if category else None,
        status=TransactionStatus.cleared,
        source=TransactionSource.starling,
    )
    session.add(row)
    session.flush()
    return row


def test_spent_so_far_reconciles_to_all_eligible_scoped_ledger(session, household):
    a, b, c, other, budget, line = fixture(session, household)
    tx(session, a, c, -1000)
    tx(session, a, other, -2000)
    tx(session, a, None, -3000)
    # Same computation rendered as Spent so far in Budget.tsx.
    displayed = budgets.spending_summary(session, budget)["spent_pence"]
    assert displayed == 6000, "Headline omits both unplanned-category and uncategorised spending"


def test_spent_so_far_excludes_future_dated_recorded_transactions(session, household):
    a, b, c, other, budget, line = fixture(session, household)
    if date.today() == budget.end_date:
        pytest.skip("Requires a future day in current month")
    tx(session, a, c, -1000)
    tx(session, a, c, -9000, date.today() + timedelta(days=1))
    assert budgets.spending_summary(session, budget)["spent_pence"] == 1000
    assert budgets.actuals_for_budget(session, budget)[line.id] == 10000


def test_partial_payment_remains_in_bills_remaining(session, household):
    a, b, c, other, budget, line = fixture(session, household)
    due = date.today()
    commitment = RecurringCommitment(
        household_id=household.id,
        account_id=a.id,
        name="Synthetic bill",
        amount_pence=-10000,
        frequency=Frequency.monthly,
        next_date=due,
    )
    session.add(commitment)
    session.flush()
    movement = ExpectedMovement(
        household_id=household.id,
        account_id=a.id,
        expected_date=due,
        amount_pence=-10000,
        label="Synthetic bill",
        confidence=Confidence.scheduled,
        source_type=ExpectedSource.commitment,
        source_id=commitment.id,
    )
    session.add(movement)
    session.flush()
    payment = tx(session, a, c, -5000)
    settle.settle_occurrence(session, movement, payment, manual=True)
    assert contributions.status(session, movement).outstanding_pence == 5000
    assert budgets.bills_remaining(session, budget) == (5000, 1)


def test_report_personal_budget_variance_uses_owner_actuals(session, household):
    a, b, c, other, budget, line = fixture(session, household)
    tx(session, a, c, -1000)
    tx(session, b, c, -9000)
    assert budgets.actuals_for_budget(session, budget)[line.id] == 1000
    report = reports.build_report(session, household.id, budget.start_date, budget.end_date)
    actual = next(
        f["amount_pence"]
        for section in report["sections"]
        for f in section["figures"]
        if f["key"] == f"budget_{budget.id}_line_{line.id}"
    )
    assert actual == 9000, "Report variance must agree with same personal budget API"


def test_monthly_report_hides_other_members_private_budget_and_payee_category(tmp_path):
    with TestClient(
        create_app(Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"}))
    ) as client:
        hid = client.post("/api/v1/households", json={"name": "Synthetic household"}).json()["id"]
        client.headers["X-Household-Id"] = str(hid)
        owner = client.post(
            "/api/v1/profiles", json={"display_name": "Synthetic admin", "role": "admin"}
        ).json()
        member = client.post(
            "/api/v1/profiles", json={"display_name": "Synthetic member", "role": "member"},
            headers={"X-Profile-Id": str(owner["id"])},
        ).json()
        h = {"X-Profile-Id": str(owner["id"])}
        category = client.post(
            "/api/v1/categories", json={"name": "Synthetic secret category"}
        ).json()
        account = client.post(
            "/api/v1/accounts",
            json={
                "name": "Synthetic private",
                "type": "personal",
                "opening_balance": 0,
                "opening_date": "2025-01-01",
                "owner_profile_id": owner["id"],
            },
            headers=h,
        ).json()
        txn = client.post(
            "/api/v1/transactions",
            json={
                "account_id": account["id"],
                "date": date.today().isoformat(),
                "amount": -12345,
                "payee": "Synthetic secret payee",
                "category_id": category["id"],
            },
            headers=h,
        )
        assert txn.status_code == 201, txn.text
        budget = client.post(
            "/api/v1/budgets",
            json={
                "name": "Synthetic private budget",
                "owner_profile_id": owner["id"],
                "period_type": "custom",
                "start_date": date.today().isoformat(),
                "end_date": date.today().isoformat(),
            },
            headers=h,
        )
        assert budget.status_code == 201, budget.text
        bid = budget.json()["id"]
        line = client.post(
            f"/api/v1/budgets/{bid}/lines",
            json={"category_id": category["id"], "side": "expense", "amount": 20000},
            headers=h,
        )
        assert line.status_code == 201, line.text
        member_headers = {"X-Profile-Id": str(member["id"])}
        assert client.get(f"/api/v1/budgets/{bid}", headers=member_headers).status_code == 404
        response = client.get(
            f"/api/v1/reports/monthly?year={date.today().year}&month={date.today().month}",
            headers=member_headers,
        )
        assert response.status_code == 200, response.text
        assert "Synthetic private budget" not in response.text
        assert "Synthetic secret category" not in response.text
        assert "12345" not in response.text


def test_spent_headline_preserves_net_refund_sign(session, household):
    a, b, c, other, budget, line = fixture(session, household)
    second = BudgetLine(
        budget_id=budget.id, category_id=other.id, side=BudgetLineSide.expense, original_pence=1000
    )
    session.add(second)
    session.flush()
    tx(session, a, c, -5000)
    tx(session, a, other, -1000)
    tx(session, a, other, 2000)
    displayed = budgets.spending_summary(session, budget)["spent_pence"]
    assert displayed == 4000, (
        "A net-refund category must reduce spending, not become positive spend"
    )


def test_opening_balance_is_not_reported_as_earned_income(session, household):
    from app.domain import accounts
    from app.persistence.models import AccountType

    accounts.open_account(
        session, household.id, "Synthetic opening", AccountType.personal, 12345, date.today()
    )
    report = reports.build_report(session, household.id, date.today(), date.today())
    income = next(
        f["amount_pence"]
        for section in report["sections"]
        for f in section["figures"]
        if f["key"] == "income_total"
    )
    assert income == 0, "Opening stock balance must not appear as income earned in that period"


def test_spending_summary_exposes_itemised_bridge_and_coverage(session, household):
    a, b, c, other, budget, line = fixture(session, household)
    planned = tx(session, a, c, -1000)
    unplanned = tx(session, a, other, -2000)
    unknown = tx(session, a, None, -3000)
    tx(session, b, c, -9000)
    tx(session, a, c, -7000).status = TransactionStatus.void
    result = budgets.spending_summary(session, budget)
    assert result["spent_pence"] == 6000
    assert result["budget_covered_pence"] == 1000
    assert result["unallocated_pence"] == 5000
    assert result["uncategorised_pence"] == 3000
    assert {r["transaction_id"] for r in result["spend_items"]} == {
        planned.id,
        unplanned.id,
        unknown.id,
    }
    assert sum(r["amount_pence"] for r in result["spend_items"]) == result["spent_pence"]


def test_report_variance_drill_keeps_owner_and_refund_sign(session, household):
    a, b, c, other, budget, line = fixture(session, household)
    tx(session, a, c, -3000)
    tx(session, a, c, 1000)
    tx(session, b, c, -9000)
    report = reports.build_report(session, household.id, budget.start_date, budget.end_date)
    figure = next(
        f
        for section in report["sections"]
        for f in section["figures"]
        if f["key"] == f"budget_{budget.id}_line_{line.id}"
    )
    assert figure["amount_pence"] == 8000
    assert figure["drill"]["params"]["owner_profile_id"] == a.owner_profile_id
    assert "direction" not in figure["drill"]["params"]


def test_linked_uncategorised_refund_reduces_spend_without_reclassification(session, household):
    a, b, c, other, budget, line = fixture(session, household)
    original = tx(session, a, None, -3000, budget.start_date - timedelta(days=1))
    refund = tx(session, a, None, 1000)
    refund.refund_of_id = original.id
    session.flush()
    result = budgets.spending_summary(session, budget)
    assert result["spent_pence"] == -1000
    assert result["uncategorised_pence"] == -1000
    assert result["income_received_pence"] == 0
    assert refund.category_id is None
