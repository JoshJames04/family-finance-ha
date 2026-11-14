"""Confirmed internal category legs are economic movement, including before pairing."""

from datetime import date, timedelta

import pytest

from app.domain import accounts, analytics, budgets, forecasting, reports, splits, transfers
from app.persistence.models import (
    Account,
    BudgetLineSide,
    BudgetPeriodType,
    Category,
    Transaction,
)


def test_confirmed_internal_legs_leave_fees_and_external_transfers_in_flow(session, household):
    today = date.today()
    first = Account(
        household_id=household.id, name="Synthetic shared", type="joint", opening_date=today,
    )
    second = Account(
        household_id=household.id, name="Synthetic savings", type="savings", opening_date=today,
    )
    internal = Category(household_id=household.id, name="Internal Transfers")
    external = Category(household_id=household.id, name="Transfer")
    fee = Category(household_id=household.id, name="Synthetic fees")
    session.add_all([first, second, internal, external, fee])
    session.flush()

    def transaction(account, amount, category):
        row = Transaction(
            account_id=account.id, date=today, amount_pence=amount,
            payee="Synthetic counterparty", category_id=category.id,
        )
        session.add(row)
        session.flush()
        return row

    transaction(first, -11000, internal)
    transaction(first, 13000, internal)
    debit = transaction(first, -9000, external)
    credit = transaction(second, 9000, external)
    transfers.create_transfer(session, household.id, debit, credit)
    mixed = transaction(first, -5700, internal)
    splits.replace(session, mixed, household.id, [
        {"category_id": internal.id, "amount_pence": -5000},
        {"category_id": fee.id, "amount_pence": -700},
    ])
    transaction(first, -3000, external)
    transaction(first, 6000, external)
    budget = budgets.create_budget(
        session, household.id, "Synthetic shared", BudgetPeriodType.custom, today, today,
    )
    internal_line = budgets.add_line(session, budget, internal.id, BudgetLineSide.expense, 20000)
    fee_line = budgets.add_line(session, budget, fee.id, BudgetLineSide.expense, 1000)
    summary = budgets.spending_summary(session, budget)
    assert summary["spent_pence"] == 3700
    assert summary["income_received_pence"] == 6000
    assert budgets.actuals_for_budget(session, budget)[internal_line.id] == 0
    assert budgets.actuals_for_budget(session, budget)[fee_line.id] == 700
    assert sum(item["amount_pence"] for item in summary["spend_items"]) == 3700
    report = reports.build_report(session, household.id, today, today)
    figures = {figure["key"]: figure for section in report["sections"]
               for figure in section["figures"]}
    assert figures["income_total"]["amount_pence"] == 6000
    assert sum(figure["amount_pence"] for section in report["sections"]
               if section["key"] == "spend_by_category" for figure in section["figures"]) == 3700
    assert f"category_{internal.id}" not in figures
    analytics.rebuild(session, household.id, through=today)
    monthly = analytics.series(session, household.id, 1)[0]
    assert (monthly.income_pence, monthly.expense_pence) == (6000, 3700)
    assert monthly.net_worth_pence == -700
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    forecast = forecasting.forecast(session, household.id, from_month=next_month, months=1,
                                    history_months=1)
    assert {row.category_id: row.amount_pence for row in forecast} == {
        fee.id: 700, external.id: 3000,
    }
    # Independently sum raw ledger values: economic exclusions must not remove money.
    assert sum(row.amount_pence for row in session.query(Transaction).all()) == -700
    assert accounts.current_balance(session, first.id) == -9700
    assert accounts.current_balance(session, second.id) == 9000


def test_internal_category_drill_excludes_pure_movements_but_retains_fee_splits(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.settings import Settings
    from app.main import create_app
    from app.persistence.models import Household, TransactionSplit

    with TestClient(create_app(Settings.load({
        "FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0",
    }))) as client:
        with Session(client.app.state.engine) as session:
            household = Household(name="Synthetic")
            session.add(household)
            session.flush()
            account = Account(
                household_id=household.id, name="Synthetic", type="joint",
                opening_date=date.today(),
            )
            internal = Category(household_id=household.id, name="Internal Transfers")
            fee = Category(household_id=household.id, name="Synthetic fee")
            ordinary = Category(household_id=household.id, name="Transfer")
            session.add_all([account, internal, fee, ordinary])
            session.flush()
            rows = [Transaction(
                account_id=account.id, date=date.today(), amount_pence=amount,
                category_id=category.id, payee="Synthetic",
            ) for amount, category in [
                (-10000, internal), (-10500, internal), (-3000, ordinary),
                (4300, internal), (-4300, internal),
            ]]
            session.add_all(rows)
            session.flush()
            splits.replace(session, rows[1], household.id, [
                {"category_id": internal.id, "amount_pence": -10000},
                {"category_id": fee.id, "amount_pence": -500},
            ])
            for row, internal_amount, external_amount in [
                (rows[3], 5000, -700), (rows[4], -5000, 700),
            ]:
                session.add_all([
                    TransactionSplit(transaction_id=row.id, category_id=internal.id,
                                     amount_pence=internal_amount, position=0),
                    TransactionSplit(transaction_id=row.id, category_id=fee.id,
                                     amount_pence=external_amount, position=1),
                ])
            session.commit()
            client.headers["X-Household-Id"] = str(household.id)
            pure, mixed, external, fee_credit, receipt_debit = [row.id for row in rows]
            fee_id = fee.id
            account_id = account.id
        response = client.get("/api/v1/transactions", params={"internal": "exclude"})
        assert response.status_code == 200, response.text
        assert {row["id"] for row in response.json()["items"]} == {
            mixed, external, fee_credit, receipt_debit,
        }
        response = client.get("/api/v1/transactions", params={"internal": "only"})
        assert {row["id"] for row in response.json()["items"]} == {
            pure, mixed, fee_credit, receipt_debit,
        }
        response = client.get("/api/v1/transactions", params={
            "internal": "exclude", "category_id": fee_id,
        })
        assert {row["id"] for row in response.json()["items"]} == {
            mixed, fee_credit, receipt_debit,
        }
        response = client.get("/api/v1/transactions", params={
            "internal": "exclude", "category_id": fee_id, "direction": "expense",
        })
        assert {row["id"] for row in response.json()["items"]} == {mixed, fee_credit}
        response = client.get("/api/v1/transactions", params={
            "internal": "exclude", "direction": "income",
        })
        assert {row["id"] for row in response.json()["items"]} == {receipt_debit}
        report = client.get(
            f"/api/v1/reports/monthly?year={date.today().year}&month={date.today().month}",
        ).json()
        figures = {figure["key"]: figure for section in report["sections"]
                   for figure in section["figures"]}
        assert figures["transfer_total"]["amount_pence"] == 25000
        assert figures["transfer_count"]["count"] == 4
        for key, wanted in [
            ("transfer_total", {pure, mixed, receipt_debit}),
            ("transfer_count", {pure, mixed, fee_credit, receipt_debit}),
        ]:
            drill = figures[key]["drill"]
            response = client.get(drill["endpoint"], params=drill["params"])
            assert {row["id"] for row in response.json()["items"]} == wanted
        with Session(client.app.state.engine) as session:
            refund = Transaction(account_id=account_id, date=date.today(), amount_pence=100,
                                 category_id=fee_id, payee="Synthetic refund", refund_of_id=mixed)
            unrelated = Transaction(account_id=account_id, date=date.today(), amount_pence=200,
                                    category_id=fee_id, payee="Synthetic unrelated receipt")
            session.add_all([refund, unrelated])
            session.commit()
            refund_id, unrelated_id = refund.id, unrelated.id
        response = client.get("/api/v1/transactions", params={
            "internal": "exclude", "category_id": fee_id, "budget_side": "expense",
        })
        assert {row["id"] for row in response.json()["items"]} == {mixed, fee_credit, refund_id}
        response = client.get("/api/v1/transactions", params={
            "internal": "exclude", "category_id": fee_id, "budget_side": "income",
        })
        assert {row["id"] for row in response.json()["items"]} == {receipt_debit, unrelated_id}
        # Ledger filters remain independent: excluding internal movements
        # must not silently apply the separate void/opening exclusions.
        with Session(client.app.state.engine) as session:
            void = Transaction(account_id=account_id, date=date.today(), amount_pence=-123,
                               category_id=fee_id, payee="Synthetic void external", status="void")
            stock = Transaction(account_id=account_id, date=date.today(), amount_pence=456,
                                category_id=fee_id, payee="Synthetic opening external",
                                source="system", external_ref="open:synthetic")
            session.add_all([void, stock])
            session.commit()
            void_id, stock_id = void.id, stock.id
        for query, wanted in [
            ({"internal": "exclude", "status": "void", "q": "Synthetic void external"}, {void_id}),
            ({"internal": "exclude", "q": "Synthetic opening external"}, {stock_id}),
            ({"internal": "exclude", "exclude_void": True, "q": "Synthetic void external"}, set()),
            ({"internal": "exclude", "exclude_opening": True,
              "q": "Synthetic opening external"}, set()),
        ]:
            response = client.get("/api/v1/transactions", params=query)
            assert {row["id"] for row in response.json()["items"]} == wanted


def test_budget_line_actuals_keep_unrelated_receipt_and_explicit_refund_separate(
    session, household,
):
    today = date.today()
    account = Account(household_id=household.id, name="Synthetic", type="joint", opening_date=today)
    category = Category(household_id=household.id, name="Synthetic mixed category")
    session.add_all([account, category])
    session.flush()
    purchase = Transaction(account_id=account.id, date=today, amount_pence=-10000,
                           category_id=category.id, payee="Synthetic shop")
    session.add(purchase)
    session.flush()
    session.add_all([
        Transaction(account_id=account.id, date=today, amount_pence=4000,
                    category_id=category.id, payee="Synthetic payer"),
        Transaction(account_id=account.id, date=today, amount_pence=1000,
                    category_id=category.id, payee="Synthetic refund", refund_of_id=purchase.id),
    ])
    session.flush()
    budget = budgets.create_budget(session, household.id, "Synthetic", BudgetPeriodType.custom,
                                    today, today)
    expense = budgets.add_line(session, budget, category.id, BudgetLineSide.expense, 20000)
    income_budget = budgets.create_budget(session, household.id, "Synthetic income",
                                           BudgetPeriodType.custom, today, today)
    income = budgets.add_line(session, income_budget, category.id, BudgetLineSide.income, 8000)
    actuals = budgets.actuals_for_budget(session, budget)
    actuals.update(budgets.actuals_for_budget(session, income_budget))
    assert actuals[expense.id] == 9000
    assert actuals[income.id] == 4000
    assert budgets.line_analysis(actuals[expense.id], expense)["variance_pence"] == 11000
    assert budgets.line_analysis(actuals[income.id], income)["variance_pence"] == 4000
    report = reports.build_report(session, household.id, today, today)
    figures = {figure["key"]: figure for section in report["sections"]
               for figure in section["figures"]}
    assert figures["income_total"]["amount_pence"] == 4000
    assert figures[f"category_{category.id}"]["amount_pence"] == 9000
    analytics.rebuild(session, household.id, through=today)
    monthly = analytics.series(session, household.id, 1)[0]
    assert (monthly.income_pence, monthly.expense_pence, monthly.savings_pence) == (
        4000, 9000, -5000,
    )
    assert monthly.net_worth_pence == -5000
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    forecast = forecasting.forecast(session, household.id, from_month=next_month, months=1,
                                    history_months=1)
    assert {row.category_id: row.amount_pence for row in forecast} == {category.id: 9000}


def test_opposite_sign_external_split_uses_effective_leg_direction(session, household):
    from app.persistence.models import TransactionSplit

    today = date.today()
    account = Account(household_id=household.id, name="Synthetic", type="joint", opening_date=today)
    internal = Category(household_id=household.id, name="Internal Transfers")
    fee = Category(household_id=household.id, name="Synthetic fee")
    receipt = Category(household_id=household.id, name="Synthetic receipt")
    session.add_all([account, internal, fee, receipt])
    session.flush()
    for parent_amount, internal_amount, external_amount, category in [
        (4300, 5000, -700, fee), (-4300, -5000, 700, receipt),
    ]:
        row = Transaction(account_id=account.id, date=today, amount_pence=parent_amount,
                          category_id=internal.id, payee="Synthetic")
        session.add(row)
        session.flush()
        session.add_all([
            TransactionSplit(transaction_id=row.id, category_id=internal.id,
                             amount_pence=internal_amount, position=0),
            TransactionSplit(transaction_id=row.id, category_id=category.id,
                             amount_pence=external_amount, position=1),
        ])
    session.flush()
    report = reports.build_report(session, household.id, today, today)
    figures = {figure["key"]: figure for section in report["sections"]
               for figure in section["figures"]}
    assert figures["income_total"]["amount_pence"] == 700
    assert figures[f"category_{fee.id}"]["amount_pence"] == 700
    assert f"category_{receipt.id}" not in figures
    assert figures["transfer_total"]["amount_pence"] == 5000
    assert figures["transfer_count"]["count"] == 2
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    forecast = forecasting.forecast(session, household.id, from_month=next_month, months=1,
                                    history_months=1)
    assert {row.category_id: row.amount_pence for row in forecast} == {fee.id: 700}


def test_anomaly_scan_ignores_confirmed_internal_movements(session, household):
    from app.domain import anomaly

    today = date.today()
    account = Account(household_id=household.id, name="Synthetic", type="joint", opening_date=today)
    internal = Category(household_id=household.id, name="Internal Transfers")
    session.add_all([account, internal])
    session.flush()
    session.add_all([Transaction(
        account_id=account.id, date=today, amount_pence=-10000,
        category_id=internal.id, payee="Synthetic own account",
    ) for _ in range(4)])
    session.flush()
    stats = anomaly.scan_account(session, account, today)
    assert stats.scanned == 0
    assert stats.flagged == 0


def test_income_percentage_excludes_unpaired_internal_credit_and_refund(session, household):
    import json

    from app.domain import transfer_rules
    from app.persistence.models import Frequency, TransferRule

    day = date(2025, 12, 15)
    source = Account(household_id=household.id, name="Synthetic source", type="joint",
                     opening_date=day)
    destination = Account(household_id=household.id, name="Synthetic destination", type="joint",
                          opening_date=day)
    internal = Category(household_id=household.id, name="Internal Transfers")
    income = Category(household_id=household.id, name="Synthetic income")
    session.add_all([source, destination, internal, income])
    session.flush()
    original = Transaction(account_id=source.id, date=day, amount_pence=-2000,
                           category_id=income.id, payee="Synthetic purchase")
    session.add(original)
    session.flush()
    session.add_all([
        Transaction(account_id=source.id, date=day, amount_pence=10000,
                    category_id=internal.id, payee="Synthetic own account"),
        Transaction(account_id=source.id, date=day, amount_pence=4000,
                    category_id=income.id, payee="Synthetic employer"),
        Transaction(account_id=source.id, date=day, amount_pence=1000,
                    category_id=income.id, payee="Synthetic refund", refund_of_id=original.id),
    ])
    rule = TransferRule(
        household_id=household.id, name="Synthetic half income", source_account_id=source.id,
        dest_account_id=destination.id, amount_spec=json.dumps({"kind": "income_pct", "bps": 5000}),
        frequency=Frequency.monthly, day_of_month=1, start_date=date(2025, 1, 1), active=True,
    )
    session.add(rule)
    session.flush()
    assert transfer_rules.evaluate(session, rule, date(2026, 1, 1)) == 2000


def test_monthly_series_is_fresh_without_rebuild_and_never_writes(session, household):
    from sqlalchemy import event, select

    from app.persistence.models import MonthlyAggregate

    today = date.today()
    account = Account(household_id=household.id, name="Synthetic", type="joint", opening_date=today)
    internal = Category(household_id=household.id, name="Internal Transfers")
    ordinary = Category(household_id=household.id, name="Synthetic expense")
    session.add_all([account, internal, ordinary])
    session.flush()
    purchase = Transaction(account_id=account.id, date=today, amount_pence=-10000,
                           category_id=ordinary.id, payee="Synthetic shop")
    session.add(purchase)
    session.flush()
    session.add_all([
        Transaction(account_id=account.id, date=today, amount_pence=3000,
                    category_id=ordinary.id, payee="Synthetic refund", refund_of_id=purchase.id),
        Transaction(account_id=account.id, date=today, amount_pence=4000,
                    category_id=ordinary.id, payee="Synthetic unrelated receipt"),
        Transaction(account_id=account.id, date=today, amount_pence=9000,
                    category_id=internal.id, payee="Synthetic own account"),
    ])
    session.flush()
    writes = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(session.bind, "before_cursor_execute", observe)
    try:
        row = analytics.series(session, household.id, 1)[0]
        assert (row.income_pence, row.expense_pence, row.net_worth_pence) == (4000, 7000, 6000)
        assert writes == []
        assert session.scalars(select(MonthlyAggregate)).all() == []
    finally:
        event.remove(session.bind, "before_cursor_execute", observe)
    analytics.rebuild(session, household.id)
    session.add(Transaction(account_id=account.id, date=today, amount_pence=-500,
                            category_id=ordinary.id, payee="Synthetic new expense"))
    session.flush()
    event.listen(session.bind, "before_cursor_execute", observe)
    try:
        row = analytics.series(session, household.id, 1)[0]
        assert (row.income_pence, row.expense_pence, row.net_worth_pence) == (4000, 7500, 5500)
        assert writes == []
        assert session.scalars(select(MonthlyAggregate)).one().expense_pence == 7000
    finally:
        event.remove(session.bind, "before_cursor_execute", observe)


@pytest.mark.parametrize("opening, balance", [(-10000, -9000), (10000, 11000)])
def test_system_opening_stock_is_not_monthly_income_or_anomaly_spending(
    session, household, opening, balance,
):
    from app.domain import anomaly
    from app.persistence.models import AccountType

    account = accounts.open_account(session, household.id, "Synthetic opening", AccountType.joint,
                                    opening, date.today())
    session.add(Transaction(account_id=account.id, date=date.today(), amount_pence=1000,
                            payee="Synthetic earned receipt"))
    session.flush()
    row = analytics.series(session, household.id, 1)[0]
    assert (row.income_pence, row.expense_pence, row.net_worth_pence) == (1000, 0, balance)
    assert accounts.current_balance(session, account.id) == balance
    assert anomaly.scan_account(session, account, date.today()).scanned == 0


def test_uncategorised_report_drill_returns_only_uncategorised_records(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.settings import Settings
    from app.main import create_app
    from app.persistence.models import Household

    with TestClient(create_app(Settings.load({
        "FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0",
    }))) as client:
        with Session(client.app.state.engine) as session:
            household = Household(name="Synthetic")
            session.add(household)
            session.flush()
            account = Account(household_id=household.id, name="Synthetic", type="joint",
                              opening_date=date.today())
            category = Category(household_id=household.id, name="Synthetic category")
            session.add_all([account, category])
            session.flush()
            unknown = Transaction(account_id=account.id, date=date.today(), amount_pence=-100,
                                  payee="Synthetic unknown")
            known = Transaction(account_id=account.id, date=date.today(), amount_pence=-200,
                                payee="Synthetic known", category_id=category.id)
            session.add_all([unknown, known])
            session.commit()
            client.headers["X-Household-Id"] = str(household.id)
            unknown_id = unknown.id
        report = client.get(
            f"/api/v1/reports/monthly?year={date.today().year}&month={date.today().month}",
        ).json()
        figure = next(figure for section in report["sections"] for figure in section["figures"]
                      if figure["key"] == "uncategorised")
        assert figure["amount_pence"] == 100
        assert figure["drill"]["params"]["uncategorised"] is True
        response = client.get(figure["drill"]["endpoint"], params=figure["drill"]["params"])
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["items"]] == [unknown_id]


def test_anomaly_category_outlier_uses_only_current_category_split(session, household):
    from sqlalchemy import select

    from app.domain import anomaly
    from app.persistence.models import AnomalyFlag, AnomalyTrigger

    today = date.today()
    account = Account(household_id=household.id, name="Synthetic", type="joint", opening_date=today)
    food = Category(household_id=household.id, name="Synthetic food")
    rent = Category(household_id=household.id, name="Synthetic rent")
    session.add_all([account, food, rent])
    session.flush()
    session.add_all([Transaction(
        account_id=account.id, date=today - timedelta(days=day), amount_pence=-1000,
        category_id=food.id, payee="Synthetic shop",
    ) for day in range(40, 44)])
    mixed = Transaction(account_id=account.id, date=today, amount_pence=-10000,
                        category_id=food.id, payee="Synthetic shop")
    session.add(mixed)
    session.flush()
    splits.replace(session, mixed, household.id, [
        {"category_id": food.id, "amount_pence": -1000},
        {"category_id": rent.id, "amount_pence": -9000},
    ])
    anomaly.scan_account(session, account, today)
    flags = session.scalars(select(AnomalyFlag).where(
        AnomalyFlag.transaction_id == mixed.id)).all()
    assert AnomalyTrigger.category_outlier not in {flag.trigger for flag in flags}
    assert AnomalyTrigger.amount_outlier in {flag.trigger for flag in flags}
