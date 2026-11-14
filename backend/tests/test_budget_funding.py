"""Received funding and opening stock use the same authorized ledger scope."""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.api import schemas
from app.api.v1.transfer_rules import delete_rule, update_rule
from app.domain.budget_funding import budget_funding
from app.persistence.models import (
    Account,
    Budget,
    Category,
    ExpectedMatch,
    ExpectedMovement,
    Household,
    Transaction,
    TransactionSplit,
    Transfer,
    TransferRule,
    UserProfile,
)

START = date(2025, 4, 1)
END = date(2025, 4, 30)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("amount", [500, -500])
def test_deleted_rule_preserves_funding_history(session, household, scope, legacy, amount):
    member, personal, shared, _, budget = scope
    source, dest = (personal, shared) if amount > 0 else (shared, personal)
    rule = TransferRule(household_id=household.id, name="Contribution",
                        source_account_id=source.id, dest_account_id=dest.id,
                        amount_spec='{"kind":"fixed","pence":500}',
                        frequency="monthly", day_of_month=1, start_date=START)
    session.add(rule)
    session.flush()
    movement = ExpectedMovement(household_id=household.id, account_id=shared.id,
                                expected_date=START, amount_pence=amount, label="Contribution",
                                confidence="scheduled", source_type="transfer_rule",
                                source_id=rule.id)
    session.add(movement)
    session.flush()
    transaction = txn(session, shared, amount)
    session.add(ExpectedMatch(movement_id=movement.id, transaction_id=transaction.id,
                              matched_pence=300, manual=True))
    session.flush()
    before = budget_funding(session, budget, [shared.id], start=START, end=END)
    if legacy:
        session.delete(rule)
        session.flush()
    else:
        delete_rule(rule.id, household, session, "synthetic-test")
    session.expire_all()
    after = budget_funding(session, budget, [shared.id], start=START, end=END)
    assert after["external_income_pence"] == before["external_income_pence"]
    assert after["income_items"] == before["income_items"]
    if legacy:
        assert after["contributions_pence"] == 0
        assert after["unattributed_internal_pence"] == (300 if amount > 0 else -300)
    else:
        assert after == before
        assert after["contributions_pence"] == (300 if amount > 0 else -300)
        assert all(i["contributor_profile_id"] is None for i in after["contribution_items"])


@pytest.mark.parametrize("endpoint", ["source_account_id", "dest_account_id",
                                     "swap_future", "source_future"])
def test_endpoint_edit_then_delete_preserves_original_funding(
        session, household, scope, endpoint):
    _, personal, shared, space, budget = scope
    rule = TransferRule(household_id=household.id, name="Contribution",
                        source_account_id=personal.id, dest_account_id=shared.id,
                        amount_spec='{"kind":"fixed","pence":500}',
                        frequency="monthly", day_of_month=1, start_date=START)
    session.add(rule)
    session.flush()
    movement = ExpectedMovement(household_id=household.id, account_id=shared.id,
                                expected_date=START, amount_pence=500, label="Contribution",
                                confidence="scheduled", source_type="transfer_rule",
                                source_id=rule.id)
    session.add(movement)
    session.flush()
    transaction = txn(session, shared, 500)
    session.add(ExpectedMatch(movement_id=movement.id, transaction_id=transaction.id,
                              matched_pence=300, manual=True))
    changes = {endpoint: space.id}
    if endpoint.endswith("_future"):
        movement.expected_date = date.today() + timedelta(days=1)
        rule.day_of_month = movement.expected_date.day
        changes = ({"source_account_id": shared.id, "dest_account_id": personal.id}
                   if endpoint == "swap_future" else {"source_account_id": space.id})
    session.flush()
    before = budget_funding(session, budget, [personal.id, shared.id], start=START, end=END)
    if endpoint.endswith("_future"):
        with pytest.raises(HTTPException) as caught:
            update_rule(rule.id, schemas.TransferRulePatch(**changes),
                        household, session, "synthetic-test")
        assert caught.value.status_code == 409
        assert (rule.source_account_id, rule.dest_account_id) == (personal.id, shared.id)
        assert movement.amount_pence == 500
        assert budget_funding(session, budget, [personal.id, shared.id],
                              start=START, end=END) == before
        return
    update_rule(rule.id, schemas.TransferRulePatch(**changes),
                household, session, "synthetic-test")
    session.expire_all()
    assert budget_funding(session, budget, [personal.id, shared.id],
                          start=START, end=END) == before
    delete_rule(rule.id, household, session, "synthetic-test")
    session.expire_all()
    assert budget_funding(session, budget, [personal.id, shared.id],
                          start=START, end=END) == before


@pytest.fixture()
def scope(session, household):
    member = UserProfile(household_id=household.id, display_name="Member")
    session.add(member)
    session.flush()
    personal = Account(household_id=household.id, name="Private salary account",
                       type="personal", owner_profile_id=member.id,
                       opening_date=date(2025, 1, 1), opening_balance_pence=999999)
    shared = Account(household_id=household.id, name="Shared", type="joint",
                     opening_date=date(2025, 1, 1))
    space = Account(household_id=household.id, name="Space", type="joint",
                    opening_date=date(2025, 1, 1))
    budget = Budget(household_id=household.id, name="April", period_type="monthly",
                    start_date=START, end_date=END, status="approved")
    session.add_all([personal, shared, space, budget])
    session.flush()
    return member, personal, shared, space, budget


def txn(session, account, amount, *, on=START, **kw):
    row = Transaction(account_id=account.id, date=on, amount_pence=amount,
                      payee="Synthetic receipt", status="cleared", source="manual")
    for key, value in kw.items():
        setattr(row, key, value)
    session.add(row)
    session.flush()
    return row


def move(session, household, source, dest, amount):
    transfer = Transfer(household_id=household.id)
    session.add(transfer)
    session.flush()
    txn(session, source, -amount, transfer_id=transfer.id)
    return txn(session, dest, amount, transfer_id=transfer.id)


def test_opening_is_strict_ledger_stock_not_income_or_account_metadata(
        session, household, scope):
    member, personal, shared, space, budget = scope
    budget.owner_profile_id = member.id
    txn(session, personal, 400, on=date(2025, 3, 1), source="system", external_ref="open:1")
    txn(session, personal, -600, on=date(2025, 3, 31))
    txn(session, personal, 999, on=date(2025, 3, 31), status="void")
    txn(session, personal, 50)
    txn(session, shared, 800, on=date(2025, 3, 31))
    old_transfer = move(session, household, personal, shared, 75)
    for leg in session.query(Transaction).filter_by(transfer_id=old_transfer.transfer_id):
        leg.date = date(2025, 3, 31)
    session.flush()
    result = budget_funding(session, budget, [personal.id, shared.id], start=START, end=END)
    assert result["opening_funds_pence"] == -275
    assert result["external_income_pence"] == 50
    assert result["income_items"][0]["transaction_id"] is not None
    assert not session.new and not session.dirty


def test_all_income_includes_unplanned_splits_but_excludes_refunds_void_opening_internal(
        session, household, scope):
    member, personal, shared, space, budget = scope
    external = Category(household_id=household.id, name="Other income")
    internal = Category(household_id=household.id, name="Internal Transfers")
    session.add_all([external, internal])
    session.flush()
    txn(session, personal, 300)
    mixed = txn(session, shared, 150)
    session.add_all([
        TransactionSplit(transaction_id=mixed.id, category_id=external.id,
                         amount_pence=100, position=0),
        TransactionSplit(transaction_id=mixed.id, category_id=internal.id,
                         amount_pence=50, position=1)])
    debit = txn(session, shared, -40)
    txn(session, shared, 40, refund_of_id=debit.id)
    txn(session, shared, 999, status="void")
    txn(session, shared, 999, source="system", external_ref="open:2")
    move(session, household, personal, shared, 200)
    session.flush()
    result = budget_funding(session, budget, [personal.id, shared.id],
                            start=START, end=END, include_private=True)
    assert result["external_income_pence"] == 400
    assert sum(i["amount_pence"] for i in result["income_items"]) == 400
    assert {i["category_id"] for i in result["income_items"]} == {None, external.id}
    assert result["contributions_pence"] == 0
    assert result["unattributed_internal_pence"] == 50


def test_shared_verified_boundary_signed_withdrawal_and_private_attribution(
        session, household, scope):
    member, personal, shared, space, budget = scope
    received = move(session, household, personal, shared, 500)
    move(session, household, shared, personal, 125)
    move(session, household, shared, space, 250)
    txn(session, shared, 30)
    result = budget_funding(session, budget, [shared.id, space.id], start=START, end=END)
    assert result["contributions_pence"] == 375
    assert result["external_income_pence"] == 30
    assert result["unattributed_internal_pence"] == 0
    assert len(result["contribution_items"]) == 2
    assert result["contribution_items"][0]["transaction_id"] == received.id
    assert all(i["contributor_profile_id"] is None for i in result["contribution_items"])
    assert "Private salary account" not in str(result)
    authorized = budget_funding(session, budget, [personal.id, shared.id, space.id],
                                start=START, end=END)
    assert all(i["contributor_profile_id"] == member.id
               for i in authorized["contribution_items"])


def test_unknown_internal_remains_separate_without_guessing_payee(session, household, scope):
    member, personal, shared, space, budget = scope
    internal = Category(household_id=household.id, name="Internal Transfers")
    session.add(internal)
    session.flush()
    txn(session, shared, 450, category_id=internal.id, payee="Member contribution")
    result = budget_funding(session, budget, [shared.id], start=START, end=END)
    assert result["contributions_pence"] == 0
    assert result["external_income_pence"] == 0
    assert result["unattributed_internal_pence"] == 450


def test_confirmed_expected_contribution_is_internal_and_auto_match_is_not_evidence(
        session, household, scope):
    member, personal, shared, space, budget = scope
    rule = TransferRule(household_id=household.id, name="Contribution",
                        source_account_id=personal.id, dest_account_id=shared.id,
                        amount_spec='{"kind":"fixed","pence":500}',
                        frequency="monthly", day_of_month=1, start_date=START)
    session.add(rule)
    session.flush()
    movement = ExpectedMovement(household_id=household.id, account_id=shared.id,
                                expected_date=START, amount_pence=500, label="Contribution",
                                confidence="scheduled", source_type="transfer_rule",
                                source_id=rule.id)
    session.add(movement)
    session.flush()
    confirmed = txn(session, shared, 500)
    session.add(ExpectedMatch(movement_id=movement.id, transaction_id=confirmed.id,
                              matched_pence=500, manual=True))
    session.flush()
    result = budget_funding(session, budget, [shared.id], start=START, end=END)
    assert result["external_income_pence"] == 0
    assert result["contributions_pence"] == 500
    assert result["unattributed_internal_pence"] == 0
    session.query(ExpectedMatch).one().manual = False
    session.flush()
    result = budget_funding(session, budget, [shared.id], start=START, end=END)
    assert result["contributions_pence"] == 0
    assert result["external_income_pence"] == 500
    match = session.query(ExpectedMatch).one()
    match.manual = True
    match.matched_pence = 300
    session.flush()
    result = budget_funding(session, budget, [shared.id], start=START, end=END)
    assert result["contributions_pence"] == 300
    assert result["external_income_pence"] == 200
    assert result["income_items"][0]["amount_pence"] == 200
    internal = Category(household_id=household.id, name="Internal Transfers")
    external = Category(household_id=household.id, name="Receipt")
    session.add_all([internal, external])
    session.flush()
    session.add_all([
        TransactionSplit(transaction_id=confirmed.id, category_id=internal.id,
                         amount_pence=200, position=0),
        TransactionSplit(transaction_id=confirmed.id, category_id=external.id,
                         amount_pence=300, position=1)])
    session.flush()
    result = budget_funding(session, budget, [shared.id], start=START, end=END)
    assert result["contributions_pence"] == 300
    assert result["external_income_pence"] == 200
    debit = txn(session, shared, -500)
    confirmed.refund_of_id = debit.id
    session.flush()
    result = budget_funding(session, budget, [shared.id], start=START, end=END)
    assert result["contributions_pence"] == 0


def test_confirmed_expected_withdrawal_is_signed_and_malformed_pair_stays_unknown(
        session, household, scope):
    member, personal, shared, space, budget = scope
    rule = TransferRule(household_id=household.id, name="Withdrawal",
                        source_account_id=shared.id, dest_account_id=personal.id,
                        amount_spec='{"kind":"fixed","pence":125}',
                        frequency="monthly", day_of_month=1, start_date=START)
    session.add(rule)
    session.flush()
    movement = ExpectedMovement(household_id=household.id, account_id=shared.id,
                                expected_date=START, amount_pence=-125, label="Withdraw",
                                confidence="scheduled", source_type="transfer_rule",
                                source_id=rule.id)
    session.add(movement)
    session.flush()
    withdrawal = txn(session, shared, -150)
    session.add(ExpectedMatch(movement_id=movement.id, transaction_id=withdrawal.id,
                              matched_pence=125, manual=True))
    transfer = Transfer(household_id=household.id)
    session.add(transfer)
    session.flush()
    txn(session, shared, 100, transfer_id=transfer.id)
    # A void counterleg cannot verify personal-to-shared funding.
    txn(session, personal, -100, transfer_id=transfer.id, status="void")
    session.flush()
    result = budget_funding(session, budget, [personal.id, shared.id], start=START, end=END)
    assert result["contributions_pence"] == -125
    assert result["contribution_items"][0]["amount_pence"] == -125
    assert result["unattributed_internal_pence"] == 100
    assert result["external_income_pence"] == 0


def test_empty_foreign_and_future_scope(session, household, scope):
    member, personal, shared, space, budget = scope
    other = Household(name="Other")
    session.add(other)
    session.flush()
    foreign = Account(household_id=other.id, name="Foreign", type="joint", opening_date=START)
    session.add(foreign)
    session.flush()
    txn(session, foreign, 1000)
    txn(session, shared, 1000, on=date(2099, 1, 1))
    for ids in ([], [foreign.id], [shared.id, foreign.id]):
        result = budget_funding(session, budget, ids, start=START, end=date(2099, 1, 31))
        assert result["external_income_pence"] == 0
        assert result["opening_funds_pence"] == 0


def test_future_opening_stock_is_as_of_today_and_excludes_future_ledger(session, scope):
    member, personal, shared, space, budget = scope
    txn(session, shared, 75, on=date.today())
    txn(session, shared, 9000, on=date(2098, 12, 31))
    result = budget_funding(session, budget, [shared.id],
                            start=date(2099, 1, 1), end=date(2099, 1, 31))
    assert result["opening_funds_pence"] == 75
    assert result["opening_as_of"] == date.today()


@pytest.mark.parametrize("target", ["space", "personal"])
def test_confirmed_expected_movement_does_not_turn_internal_receipt_into_income(
        session, household, scope, target):
    member, personal, shared, space, budget = scope
    dest = space if target == "space" else personal
    if target == "personal":
        budget.owner_profile_id = member.id
    rule = TransferRule(household_id=household.id, name="Internal movement",
                        source_account_id=shared.id, dest_account_id=dest.id,
                        amount_spec='{"kind":"fixed","pence":500}',
                        frequency="monthly", day_of_month=1, start_date=START)
    session.add(rule)
    session.flush()
    movement = ExpectedMovement(household_id=household.id, account_id=dest.id,
                                expected_date=START, amount_pence=500, label="Move",
                                confidence="scheduled", source_type="transfer_rule",
                                source_id=rule.id)
    session.add(movement)
    session.flush()
    received = txn(session, dest, 500)
    session.add(ExpectedMatch(movement_id=movement.id, transaction_id=received.id,
                              matched_pence=500, manual=True))
    session.flush()
    result = budget_funding(session, budget, [shared.id, dest.id], start=START, end=END)
    assert result["contributions_pence"] == 0
    assert result["external_income_pence"] == 0
    assert result["unattributed_internal_pence"] == 0


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("opposing_internal", [False, True])
def test_match_consumes_only_effective_legs_in_movement_direction(
        session, household, scope, direction, opposing_internal):
    member, personal, shared, space, budget = scope
    source, dest = (personal, shared) if direction > 0 else (shared, personal)
    rule = TransferRule(household_id=household.id, name="Mixed movement",
                        source_account_id=source.id, dest_account_id=dest.id,
                        amount_spec='{"kind":"fixed","pence":125}',
                        frequency="monthly", day_of_month=1, start_date=START)
    same = Category(household_id=household.id, name="Same direction")
    opposite = Category(household_id=household.id,
                        name="Internal Transfers" if opposing_internal else "Opposing leg")
    session.add_all([rule, same, opposite])
    session.flush()
    movement = ExpectedMovement(household_id=household.id, account_id=shared.id,
                                expected_date=START, amount_pence=125 * direction,
                                label="Move", confidence="scheduled",
                                source_type="transfer_rule", source_id=rule.id)
    session.add(movement)
    session.flush()
    mixed = txn(session, shared, 150 * direction)
    session.add_all([
        TransactionSplit(transaction_id=mixed.id, category_id=opposite.id,
                         amount_pence=-50 * direction, position=0),
        TransactionSplit(transaction_id=mixed.id, category_id=same.id,
                         amount_pence=200 * direction, position=1),
        ExpectedMatch(movement_id=movement.id, transaction_id=mixed.id,
                      matched_pence=125, manual=True)])
    session.flush()
    result = budget_funding(session, budget, [personal.id, shared.id], start=START, end=END)
    assert result["contributions_pence"] == 125 * direction
    assert result["external_income_pence"] == (
        75 if direction > 0 else (0 if opposing_internal else 50))
    assert result["unattributed_internal_pence"] == (
        -50 * direction if opposing_internal else 0)
