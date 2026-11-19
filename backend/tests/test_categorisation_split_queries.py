"""Bulk split protection loads child rows once instead of per transaction."""

from datetime import date, timedelta

import pytest
from sqlalchemy import event, inspect

from app.domain import categorise, splits
from app.persistence.models import (
    Account,
    Category,
    ExpectedMatch,
    ExpectedMovement,
    RecurringCommitment,
    Transaction,
)


@pytest.mark.parametrize("operation", ["rules", "provider", "owned", "pair", "inherit"])
@pytest.mark.parametrize("transaction_count", [20, 100])
def test_bulk_split_protection_uses_one_child_query(
    session, household, operation, transaction_count
):
    accounts = [Account(household_id=household.id, name=name, type="joint",
                        opening_date=date(2025, 1, 1)) for name in ("Current", "Bills")]
    categories = [Category(household_id=household.id, name=name)
                  for name in ("Food", "Fee", "Internal Transfers")]
    session.add_all(accounts + categories)
    session.flush()
    commitments = [RecurringCommitment(household_id=household.id, account_id=a.id,
                                       name="Scheduled payment", amount_pence=-100,
                                       frequency="monthly", next_date=date(2025, 1, 1),
                                       category_id=categories[0].id) for a in accounts]
    session.add_all(commitments)
    session.flush()
    transactions = []
    for index in range(transaction_count):
        side = index % 2
        sign = 1 if side else -1
        day = date(2025, 1, 1) + timedelta(days=index // 2)
        transaction = Transaction(account_id=accounts[side].id, date=day,
                                  amount_pence=sign * 100, payee=accounts[1-side].name,
                                  status="cleared", provider_category="GENERAL")
        session.add(transaction)
        session.flush()
        splits.replace(session, transaction, household.id, [
            {"category_id": categories[0].id, "amount_pence": sign * 60},
            {"category_id": categories[1].id, "amount_pence": sign * 40},
        ])
        if operation == "inherit":
            movement = ExpectedMovement(household_id=household.id,
                                        account_id=transaction.account_id,
                                        expected_date=day, amount_pence=transaction.amount_pence,
                                        label="Scheduled payment", confidence="scheduled",
                                        source_type="commitment", source_id=commitments[side].id)
            session.add(movement)
            session.flush()
            session.add(ExpectedMatch(movement_id=movement.id, transaction_id=transaction.id,
                                      matched_pence=100, manual=True))
        transactions.append(transaction)
    session.flush()
    household_id = household.id
    session.expire_all()
    assert all("splits" in inspect(transaction).unloaded for transaction in transactions)
    split_selects = []

    def record_split_select(connection, cursor, statement, parameters, context, executemany):
        sql = " ".join(statement.lower().split())
        if sql.startswith("select") and "from transaction_split " in sql:
            split_selects.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record_split_select)
    try:
        if operation == "rules":
            categorise.apply_rules(session, household_id)
        elif operation == "provider":
            categorise.apply_provider_map(session, household_id)
        elif operation == "owned":
            categorise.classify_owned_movements(session, household_id)
        elif operation == "pair":
            categorise.auto_pair_transfers(session, household_id)
        else:
            categorise.inherit_match_categories(session, household_id)
    finally:
        event.remove(engine, "before_cursor_execute", record_split_select)
    # Count only the new split-eligibility overhead, not unrelated pre-existing queries.
    assert len(split_selects) == 1
