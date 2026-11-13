"""Confirmed movement evidence must classify the same economic reporting legs."""
from datetime import date

import pytest
from sqlalchemy import select

from app.domain.reporting import external_category_amounts
from app.persistence.models import (
    Account,
    Category,
    ExpectedMatch,
    ExpectedMovement,
    Household,
    Transaction,
    TransactionSplit,
    Transfer,
    TransferRule,
)

DAY = date(2025, 4, 1)


@pytest.fixture()
def evidence(session, household):
    source = Account(household_id=household.id, name="Source", type="joint", opening_date=DAY)
    dest = Account(household_id=household.id, name="Destination", type="joint", opening_date=DAY)
    internal = Category(household_id=household.id, name="Internal Transfers")
    external = Category(household_id=household.id, name="Economic")
    session.add_all([source, dest, internal, external])
    session.flush()
    rule = TransferRule(household_id=household.id, name="Confirmed", source_account_id=source.id,
                        dest_account_id=dest.id, amount_spec='{"kind":"fixed","pence":125}',
                        frequency="monthly", day_of_month=1, start_date=DAY)
    session.add(rule)
    session.flush()
    return source, dest, internal, external, rule


def confirmed(session, household, evidence, amount, legs=None):
    source, dest, internal, external, rule = evidence
    account = dest if amount > 0 else source
    tx = Transaction(account_id=account.id, date=DAY, amount_pence=amount,
                     payee="Synthetic", source="manual", status="cleared", category_id=external.id)
    movement = ExpectedMovement(household_id=household.id, account_id=account.id,
                                expected_date=DAY, amount_pence=125 if amount > 0 else -125,
                                label="Confirmed", confidence="scheduled",
                                source_type="transfer_rule", source_id=rule.id)
    session.add_all([tx, movement])
    session.flush()
    match = ExpectedMatch(movement_id=movement.id, transaction_id=tx.id,
                          matched_pence=125, manual=True)
    session.add(match)
    for position, (category, leg_amount) in enumerate(legs or []):
        session.add(TransactionSplit(transaction_id=tx.id, category_id=category.id,
                                     amount_pence=leg_amount, position=position))
    session.flush()
    return tx, movement, match


def amounts(session, tx, **kwargs):
    query = external_category_amounts(Transaction.id == tx.id, **kwargs)
    return sorted(session.execute(select(query.c.amount_pence)).scalars())


@pytest.mark.parametrize("amount, expected", [(150, [25]), (-150, [-25])])
def test_partial_confirmed_amount_is_internal(session, household, evidence, amount, expected):
    tx, _, _ = confirmed(session, household, evidence, amount)
    assert amounts(session, tx) == expected
    assert amounts(session, tx, classify_confirmed=False) == [amount]


def test_negative_parent_preserves_opposing_receipt(session, household, evidence):
    external = evidence[3]
    tx, _, _ = confirmed(session, household, evidence, -150,
                         [(external, 50), (external, -200)])
    assert amounts(session, tx) == [-75, 50]


def test_internal_legs_consume_match_before_duplicate_external_categories(
        session, household, evidence):
    internal, external = evidence[2:4]
    tx, _, _ = confirmed(session, household, evidence, 150,
                         [(external, 50), (internal, 100), (external, 25), (external, -25)])
    assert sum(amounts(session, tx)) == 25
    assert -25 in amounts(session, tx)


@pytest.mark.parametrize("invalid", ["automatic", "refund", "wrong_account", "wrong_sign",
                                     "foreign_movement", "foreign_rule", "foreign_source",
                                     "foreign_dest", "wrong_role", "paired"])
def test_invalid_evidence_does_not_consume_external(session, household, evidence, invalid):
    tx, movement, match = confirmed(session, household, evidence, 150)
    foreign = Household(name="Foreign")
    session.add(foreign)
    session.flush()
    source, dest, _, _, rule = evidence
    if invalid == "automatic":
        match.manual = False
    elif invalid == "refund":
        tx.refund_of_id = tx.id
    elif invalid == "wrong_account":
        movement.account_id = source.id
    elif invalid == "wrong_sign":
        movement.amount_pence = -125
    elif invalid == "foreign_movement":
        movement.household_id = foreign.id
    elif invalid == "foreign_rule":
        rule.household_id = foreign.id
    elif invalid == "foreign_source":
        source.household_id = foreign.id
    elif invalid == "foreign_dest":
        dest.household_id = foreign.id
    elif invalid == "wrong_role":
        tx.account_id = source.id
        movement.account_id = source.id
    elif invalid == "paired":
        transfer = Transfer(household_id=household.id)
        session.add(transfer)
        session.flush()
        tx.transfer_id = transfer.id
    session.flush()
    assert amounts(session, tx) == ([] if invalid == "paired" else [150])


def test_already_internal_and_capped_match(session, household, evidence):
    tx, movement, match = confirmed(session, household, evidence, 150)
    tx.category_id = evidence[2].id
    assert amounts(session, tx) == []
    tx.category_id = evidence[3].id
    match.matched_pence = 200
    session.flush()
    assert amounts(session, tx) == []


def test_multiple_confirmations_sum_and_never_widen_account_scope(session, household, evidence):
    tx, movement, match = confirmed(session, household, evidence, 150)
    match.matched_pence = 60
    second = ExpectedMovement(household_id=household.id, account_id=tx.account_id,
                              expected_date=date(2025, 4, 2), amount_pence=100, label="Second",
                              confidence="scheduled", source_type="transfer_rule",
                              source_id=evidence[4].id)
    session.add(second)
    session.flush()
    session.add(ExpectedMatch(movement_id=second.id, transaction_id=tx.id,
                              matched_pence=40, manual=True))
    session.flush()
    assert amounts(session, tx) == [50]
    query = external_category_amounts(Account.id == evidence[0].id)
    assert list(session.execute(select(query))) == []
    assert not session.new and not session.dirty
