"""Only live eligible economic payments satisfy expected movements (#494)."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from app.core.settings import Settings
from app.domain import (
    budgets,
    categorise,
    contributions,
    notifications,
    reconcile,
    settle,
    transactions,
)
from app.domain.budget_funding import budget_funding
from app.domain.reporting import external_category_amounts
from app.main import create_app
from app.persistence.models import (
    Account,
    Budget,
    Category,
    Confidence,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Household,
    NotificationEvent,
    NotificationKind,
    RecurringCommitment,
    Transaction,
    TransactionStatus,
    Transfer,
    TransferRule,
    UserProfile,
)

DAY = date(2025, 4, 10)


@pytest.fixture()
def payment(session, household):
    account = Account(household_id=household.id, name="Synthetic", type="joint", opening_date=DAY)
    session.add(account)
    session.flush()
    movement = ExpectedMovement(household_id=household.id, account_id=account.id,
        expected_date=DAY, amount_pence=1000, label="Synthetic", confidence=Confidence.scheduled,
        source_type=ExpectedSource.employment, source_id=1)
    tx = Transaction(account_id=account.id, date=DAY, amount_pence=1000,
                     payee="Synthetic", status=TransactionStatus.cleared, source="manual")
    session.add_all([movement, tx])
    session.flush()
    return account, movement, tx


def link(session, movement, tx, amount=1000):
    match = ExpectedMatch(movement_id=movement.id, transaction_id=tx.id,
                          matched_pence=amount, manual=True)
    session.add(match)
    session.flush()
    return match


@pytest.mark.parametrize("writer", ["manual", "settle"])
def test_void_payment_cannot_create_match_or_settlement(session, payment, writer):
    _, movement, tx = payment
    tx.status = TransactionStatus.void
    session.flush()
    with pytest.raises(contributions.MatchError):
        if writer == "manual":
            contributions.confirm(session, movement, tx)
        else:
            settle.settle_occurrence(session, movement, tx, manual=True)
    assert session.scalar(select(func.count()).select_from(ExpectedMatch)) == 0


@pytest.mark.parametrize("invalid", ["void", "sign", "zero", "account", "household", "refund"])
def test_historical_invalid_links_do_not_satisfy_payment(session, household, payment, invalid):
    account, movement, tx = payment
    match = link(session, movement, tx)
    if invalid == "void":
        tx.status = TransactionStatus.void
    elif invalid == "sign":
        tx.amount_pence = -1000
    elif invalid == "zero":
        tx.amount_pence = 0
    elif invalid == "account":
        other = Account(household_id=household.id, name="Other", type="joint", opening_date=DAY)
        session.add(other)
        session.flush()
        tx.account_id = other.id
    elif invalid == "household":
        other = Household(name="Other")
        session.add(other)
        session.flush()
        account.household_id = other.id
    else:
        purchase = Transaction(account_id=account.id, date=DAY, amount_pence=-1000,
                               payee="Purchase", source="manual")
        session.add(purchase)
        session.flush()
        tx.refund_of_id = purchase.id
    session.flush()
    assert contributions.status(session, movement).outstanding_pence == 1000
    assert session.get(ExpectedMatch, match.id) is match  # audit evidence is retained


def test_live_amount_caps_historical_claim_and_original_partial_amount(session, payment):
    _, movement, tx = payment
    match = link(session, movement, tx, 600)
    tx.amount_pence = 400
    session.flush()
    assert contributions.matched_total(session, movement.id) == 400
    tx.amount_pence = 900
    session.flush()
    assert contributions.matched_total(session, movement.id) == 600
    assert match.matched_pence == 600


def test_equivalent_corrections_and_historical_chains_preserve_one_payment(session, payment):
    _, movement, original = payment
    match = link(session, movement, original, 600)
    original.status = TransactionStatus.reconciled
    current = transactions.correct(session, original, "Synthetic correction", payee="Renamed")
    current.status = TransactionStatus.reconciled
    current = transactions.correct(session, current, "Repeated", notes="Synthetic")
    session.flush()
    assert contributions.status(session, movement).matched_pence == 600
    # A fresh read of an old persisted chain resolves without moving the audit FK.
    session.expire_all()
    before = session.scalar(select(func.count()).select_from(ExpectedMatch))
    assert contributions.matched_total(session, movement.id) == 600
    assert match.transaction_id == original.id
    assert session.scalar(select(func.count()).select_from(ExpectedMatch)) == before
    with pytest.raises(contributions.MatchError):
        contributions.confirm(session, movement, current)


def test_original_and_replacement_aliases_never_spend_one_payment_twice(
        session, household, payment):
    _, first, original = payment
    link(session, first, original, 700)
    original.status = TransactionStatus.reconciled
    current = transactions.correct(session, original, "Synthetic")
    second = ExpectedMovement(household_id=household.id, account_id=current.account_id,
        expected_date=DAY + timedelta(days=1), amount_pence=1000, label="Second",
        confidence="scheduled", source_type="manual", source_id=2)
    session.add(second)
    session.flush()
    link(session, second, current, 800)  # legacy duplicate evidence
    totals = contributions.matched_totals(session, [first.id, second.id])
    assert totals == {first.id: 700, second.id: 300}
    assert contributions.matched_total(session, second.id) == 300


def test_ambiguous_or_financially_changed_correction_does_not_satisfy(session, payment):
    _, movement, original = payment
    link(session, movement, original)
    original.status = TransactionStatus.reconciled
    current = transactions.correct(session, original, "Synthetic")
    branch = Transaction(account_id=original.account_id, date=DAY, amount_pence=1000,
        payee="Ambiguous", source="manual", corrects_id=original.id)
    session.add(branch)
    session.flush()
    assert contributions.matched_total(session, movement.id) == 0
    branch.status = TransactionStatus.void
    current.amount_pence = 900
    session.flush()
    assert contributions.matched_total(session, movement.id) == 0


def test_refund_rejected_for_salary_but_legitimate_manual_refund_is_allowed(session, payment):
    account, movement, tx = payment
    purchase = Transaction(account_id=account.id, date=DAY, amount_pence=-1000,
                           payee="Purchase", source="manual")
    session.add(purchase)
    session.flush()
    tx.refund_of_id = purchase.id
    for source in [ExpectedSource.employment, ExpectedSource.payroll_run]:
        movement.source_type = source
        with pytest.raises(contributions.MatchError):
            contributions.confirm(session, movement, tx)
    movement.source_type = ExpectedSource.manual
    contributions.confirm(session, movement, tx)
    tx.status = TransactionStatus.reconciled
    corrected = transactions.correct(session, tx, "Refund label correction")
    assert corrected.refund_of_id == purchase.id
    assert contributions.status(session, movement).matched_pence == 1000


def test_automatic_transfer_matching_skips_void_legs(session, household, payment):
    _, movement, tx = payment
    movement.source_type = ExpectedSource.transfer_rule
    transfer = Transfer(household_id=household.id)
    session.add(transfer)
    session.flush()
    tx.transfer_id = transfer.id
    tx.status = TransactionStatus.void
    session.flush()
    assert contributions.auto_match(session, household.id, today=DAY) == 0
    assert contributions.status(session, movement).outstanding_pence == 1000


def test_batch_totals_are_read_only_and_one_query(session, payment):
    _, movement, tx = payment
    link(session, movement, tx)
    mid = movement.id
    session.commit()
    statements = []
    def observe(_connection, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        assert contributions.matched_totals(session, [mid, 999999]) == {mid: 1000, 999999: 0}
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert len(statements) == 1
    assert not session.new and not session.dirty and not session.deleted


def test_void_match_api_returns_409_without_persisting_match(tmp_path):
    app = create_app(Settings.load({"FF_DATA_DIR": str(tmp_path)}))
    with TestClient(app) as client:
        hid = client.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
        headers = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", headers=headers, json={
            "name": "Synthetic", "type": "joint", "opening_balance": 0,
            "opening_date": DAY.isoformat()}).json()["id"]
        tx = client.post("/api/v1/transactions", headers=headers, json={
            "account_id": aid, "date": DAY.isoformat(), "amount": 1000,
            "payee": "Synthetic"}).json()
        assert client.patch(f"/api/v1/transactions/{tx['id']}", headers=headers,
                            json={"status": "void"}).status_code == 200
        movement = client.post("/api/v1/expected-movements", headers=headers, json={
            "account_id": aid, "expected_date": DAY.isoformat(), "amount": 1000,
            "label": "Synthetic"}).json()
        response = client.post(f"/api/v1/expected-movements/{movement['id']}/matches",
                               headers=headers, json={"transaction_id": tx["id"]})
        assert response.status_code == 409
        with sessionmaker(bind=app.state.engine)() as independent:
            assert independent.scalar(select(func.count()).select_from(ExpectedMatch)) == 0


@pytest.mark.parametrize("kind", [NotificationKind.bill_reminder, NotificationKind.missed_transfer])
def test_invalid_match_cannot_suppress_due_notifications(session, household, payment, kind):
    _, movement, tx = payment
    movement.source_type = ExpectedSource.transfer_rule
    movement.amount_pence = tx.amount_pence = -1000
    link(session, movement, tx)
    tx.status = TransactionStatus.void
    rule = notifications.create_rule(session, household.id, name="Synthetic", kind=kind)
    today = DAY if kind == NotificationKind.bill_reminder else DAY + timedelta(days=2)
    assert notifications.evaluate(session, rule, today=today) == 1
    assert session.scalar(select(func.count()).select_from(NotificationEvent)) == 1


@pytest.mark.parametrize("corrections", [0, 1, 2])
def test_transfer_funding_and_reports_follow_corrected_partial_evidence(
        session, household, payment, corrections):
    account, movement, tx = payment
    member = UserProfile(household_id=household.id, display_name="Synthetic", role="member")
    session.add(member)
    session.flush()
    personal = Account(household_id=household.id, name="Personal", type="personal",
                       opening_date=DAY, owner_profile_id=member.id)
    category = Category(household_id=household.id, name="External")
    session.add_all([personal, category])
    session.flush()
    rule = TransferRule(household_id=household.id, name="Synthetic",
        source_account_id=personal.id, dest_account_id=account.id,
        amount_spec='{"kind":"fixed","pence":1000}', frequency="monthly",
        day_of_month=10, start_date=DAY)
    budget = Budget(household_id=household.id, name="Shared", period_type="monthly",
                    start_date=DAY.replace(day=1), end_date=DAY.replace(day=30))
    session.add_all([rule, budget])
    session.flush()
    movement.source_type = ExpectedSource.transfer_rule
    movement.source_id = rule.id
    tx.category_id = category.id
    match = link(session, movement, tx, 600)
    original_id = tx.id
    for _ in range(corrections):
        tx.status = TransactionStatus.reconciled
        tx = transactions.correct(session, tx, "Synthetic label correction")
    session.flush()
    funding = budget_funding(session, budget, [account.id, personal.id],
                             start=budget.start_date, end=budget.end_date)
    external = external_category_amounts(Transaction.id == tx.id)
    assert funding["external_income_pence"] == 400
    assert funding["contributions_pence"] == 600
    assert funding["contribution_items"][0]["transaction_id"] == tx.id
    assert sum(session.scalars(select(external.c.amount_pence))) == 400
    assert contributions.status(session, movement).outstanding_pence == 400
    assert match.transaction_id == original_id
    assert not session.new and not session.dirty and not session.deleted


def test_invalid_partial_link_agrees_with_budget_and_category_readers(session, household, payment):
    account, movement, tx = payment
    category = Category(household_id=household.id, name="Synthetic bill")
    session.add(category)
    session.flush()
    commitment = RecurringCommitment(household_id=household.id, account_id=account.id,
        name="Synthetic", amount_pence=-1000, frequency="monthly", next_date=DAY,
        category_id=category.id)
    budget = Budget(household_id=household.id, name="Shared", period_type="monthly",
                    start_date=DAY.replace(day=1), end_date=DAY.replace(day=30))
    session.add_all([commitment, budget])
    session.flush()
    movement.source_type = ExpectedSource.commitment
    movement.source_id = commitment.id
    movement.amount_pence = tx.amount_pence = -1000
    link(session, movement, tx, 400)
    assert budgets.bills_remaining(session, budget) == (600, 1)
    tx.amount_pence = 1000  # historical invalid same-row sign change
    session.flush()
    assert budgets.bills_remaining(session, budget) == (1000, 1)
    assert categorise.inherit_match_categories(session, household.id) == 0
    assert tx.category_id is None
    tx.amount_pence = -1000
    session.flush()
    assert categorise.inherit_match_categories(session, household.id) == 1
    assert tx.category_id == category.id


def test_member_totals_skip_void_counterpart_and_follow_corrected_leg(session, household, payment):
    account, movement, tx = payment
    member = UserProfile(household_id=household.id, display_name="Synthetic", role="member")
    session.add(member)
    session.flush()
    personal = Account(household_id=household.id, name="Personal", type="personal",
                       opening_date=DAY, owner_profile_id=member.id)
    transfer = Transfer(household_id=household.id)
    session.add_all([personal, transfer])
    session.flush()
    debit = Transaction(account_id=personal.id, date=DAY, amount_pence=-1000,
        payee="Synthetic", source="manual", transfer_id=transfer.id)
    tx.transfer_id = transfer.id
    movement.source_type = ExpectedSource.transfer_rule
    session.add(debit)
    link(session, movement, tx, 700)
    def total():
        return contributions.member_contributions(session, household.id, start=DAY, end=DAY)[0]
    assert total().contributed_pence == 700
    tx.status = TransactionStatus.reconciled
    corrected = transactions.correct(session, tx, "Synthetic")
    assert total().contributed_pence == 700
    ambiguous = Transaction(account_id=personal.id, date=DAY, amount_pence=-1000,
        payee="Ambiguous old leg", source="manual", transfer_id=transfer.id)
    session.add(ambiguous)
    session.flush()
    assert total().contributed_pence == 0
    ambiguous.status = TransactionStatus.void
    debit.status = TransactionStatus.void
    session.flush()
    assert total().contributed_pence == 0
    assert corrected.status == TransactionStatus.pending


@pytest.mark.parametrize("kind", [NotificationKind.bill_reminder, NotificationKind.missed_transfer])
def test_partial_payment_notification_reports_only_unpaid_amount(session, household, payment, kind):
    _, movement, tx = payment
    movement.source_type = ExpectedSource.transfer_rule
    movement.amount_pence = tx.amount_pence = -1000
    link(session, movement, tx, 400)
    rule = notifications.create_rule(session, household.id, name="Synthetic", kind=kind)
    today = DAY if kind == NotificationKind.bill_reminder else DAY + timedelta(days=2)
    assert notifications.evaluate(session, rule, today=today) == 1
    event_row = session.scalar(select(NotificationEvent))
    assert event_row.payload["amount_pence"] == -600


def test_correction_cycles_fail_closed_without_recursion_or_writes(session, payment):
    _, movement, original = payment
    link(session, movement, original)
    original.status = TransactionStatus.reconciled
    replacement = transactions.correct(session, original, "Synthetic")
    replacement.status = TransactionStatus.void
    original.corrects_id = replacement.id  # simulate corrupt old persisted cycle
    session.flush()
    assert contributions.matched_total(session, movement.id) == 0
    assert not session.new and not session.dirty and not session.deleted


def test_weekly_digest_uses_only_remaining_valid_liability(session, household, payment):
    _, movement, tx = payment
    movement.source_type = ExpectedSource.transfer_rule
    movement.amount_pence = tx.amount_pence = -1000
    link(session, movement, tx, 400)
    rule = notifications.create_rule(session, household.id, name="Synthetic",
        kind=NotificationKind.weekly_digest, params={"weekday": DAY.weekday()})
    assert notifications.evaluate(session, rule, today=DAY) == 1
    event_row = session.scalar(select(NotificationEvent))
    assert event_row.payload["liabilities_pence"] == 600


def test_reconciliation_skips_salary_refund_without_blocking_real_salary(session, payment):
    account, movement, refund = payment
    purchase = Transaction(account_id=account.id, date=DAY, amount_pence=-1000,
                           payee="Purchase", source="manual")
    session.add(purchase)
    session.flush()
    refund.refund_of_id = purchase.id
    result = reconcile.reconcile_account(session, account, DAY)
    assert result.matched == 0
    assert contributions.matched_total(session, movement.id) == 0
    receipt = Transaction(account_id=account.id, date=DAY, amount_pence=1000,
                          payee="Synthetic", source="manual")
    session.add(receipt)
    session.flush()
    result = reconcile.reconcile_account(session, account, DAY)
    assert result.matched == 1
    assert contributions.matched_total(session, movement.id) == 1000
    assert refund.status != TransactionStatus.reconciled


def test_unassigned_expectation_cannot_count_an_account_payment_or_be_manually_matched(
        session, payment):
    _, movement, tx = payment
    movement.account_id = None
    stored = link(session, movement, tx)  # simulate invalid historical evidence
    assert contributions.status(session, movement).outstanding_pence == 1000
    with pytest.raises(contributions.MatchError, match="account"):
        contributions.confirm(session, movement, tx)
    assert session.get(ExpectedMatch, stored.id) is stored
    assert session.scalar(select(func.count()).select_from(ExpectedMatch)) == 1


@pytest.mark.parametrize("anchor", ["original", "replacement", "self"])
def test_live_correction_cycle_cannot_supply_historical_evidence(session, payment, anchor):
    _, movement, original = payment
    if anchor == "self":
        original.corrects_id = original.id
        selected = original
    else:
        original.status = TransactionStatus.reconciled
        replacement = transactions.correct(session, original, "Synthetic")
        original.corrects_id = replacement.id
        selected = original if anchor == "original" else replacement
    stored = link(session, movement, selected)
    statements = []
    def observe(_connection, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        assert contributions.matched_total(session, movement.id) == 0
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith(("WITH", "SELECT"))
    assert session.get(ExpectedMatch, stored.id) is stored
    assert not session.new and not session.dirty and not session.deleted


@pytest.mark.parametrize("writer", ["manual", "settle"])
@pytest.mark.parametrize("shape", ["direct", "alias", "self"])
def test_cyclic_payment_is_rejected_before_any_write(session, payment, writer, shape):
    _, movement, original = payment
    if shape == "self":
        current = original
        current.corrects_id = current.id
    else:
        original.status = TransactionStatus.reconciled
        current = transactions.correct(session, original, "Synthetic")
        original.corrects_id = current.id
    if shape == "alias":
        link(session, movement, original)
    session.flush()
    statements = []
    def observe(_connection, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with pytest.raises(contributions.MatchError, match="cycl"):
            if writer == "manual":
                contributions.confirm(session, movement, current)
            else:
                settle.settle_occurrence(session, movement, current, manual=True)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert all(s.lstrip().upper().startswith(("WITH", "SELECT")) for s in statements)
    assert session.scalar(select(func.count()).select_from(ExpectedMatch)) == (shape == "alias")
    assert not session.new and not session.dirty and not session.deleted


@pytest.mark.parametrize("count", [0, 1, 3])
def test_acyclic_payment_still_accepts_manual_match_after_corrections(session, payment, count):
    _, movement, current = payment
    for _ in range(count):
        current.status = TransactionStatus.reconciled
        current = transactions.correct(session, current, "Synthetic")
    contributions.confirm(session, movement, current)
    assert contributions.matched_total(session, movement.id) == 1000


def test_live_cycle_match_api_returns_409_and_keeps_database_unchanged(tmp_path):
    app = create_app(Settings.load({"FF_DATA_DIR": str(tmp_path)}))
    with TestClient(app) as client:
        hid = client.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
        headers = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", headers=headers, json={
            "name": "Synthetic", "type": "joint", "opening_balance": 0,
            "opening_date": DAY.isoformat()}).json()["id"]
        movement = client.post("/api/v1/expected-movements", headers=headers, json={
            "account_id": aid, "expected_date": DAY.isoformat(), "amount": 1000,
            "label": "Synthetic"}).json()
        with sessionmaker(bind=app.state.engine)() as seed:
            original = Transaction(account_id=aid, date=DAY, amount_pence=1000,
                payee="Synthetic", source="manual", status=TransactionStatus.reconciled)
            seed.add(original)
            seed.flush()
            replacement = transactions.correct(seed, original, "Synthetic")
            original.corrects_id = replacement.id
            replacement_id = replacement.id
            seed.commit()
        response = client.post(f"/api/v1/expected-movements/{movement['id']}/matches",
                               headers=headers, json={"transaction_id": replacement_id})
        assert response.status_code == 409
        with sessionmaker(bind=app.state.engine)() as independent:
            assert independent.scalar(select(func.count()).select_from(ExpectedMatch)) == 0


def test_cyclic_auto_match_candidates_are_skipped_without_writes(session, household, payment):
    _, movement, tx = payment
    movement.source_type = ExpectedSource.transfer_rule
    transfer = Transfer(household_id=household.id)
    session.add(transfer)
    session.flush()
    tx.transfer_id = transfer.id
    tx.corrects_id = tx.id
    session.flush()
    statements = []
    def observe(_connection, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        assert contributions.auto_match(session, household.id, today=DAY) == 0
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert all(s.lstrip().upper().startswith(("WITH", "SELECT")) for s in statements)
    assert session.scalar(select(func.count()).select_from(ExpectedMatch)) == 0


def test_cyclic_evidence_does_not_hide_unrelated_valid_payment(session, household, payment):
    account, invalid, tx = payment
    tx.corrects_id = tx.id
    link(session, invalid, tx)
    valid = ExpectedMovement(household_id=household.id, account_id=account.id,
        expected_date=DAY, amount_pence=1000, label="Valid", confidence="scheduled",
        source_type="manual", source_id=2)
    receipt = Transaction(account_id=account.id, date=DAY, amount_pence=1000,
                           payee="Synthetic valid", source="manual")
    session.add_all([valid, receipt])
    session.flush()
    link(session, valid, receipt, 400)
    assert contributions.matched_totals(session, [invalid.id, valid.id]) == {
        invalid.id: 0, valid.id: 400}
    assert contributions.matched_total(session, valid.id) == 400


def test_edited_acyclic_replacement_can_be_directly_confirmed_without_carrying_old_alias(
        session, payment):
    _, movement, original = payment
    stored = link(session, movement, original)
    original.status = TransactionStatus.reconciled
    current = transactions.correct(session, original, "Synthetic")
    current.amount_pence = 700  # explicit edit of the pending replacement
    session.flush()
    assert contributions.matched_total(session, movement.id) == 0
    confirmed = contributions.confirm(session, movement, current)
    assert confirmed.transaction_id == current.id
    assert stored.transaction_id == original.id
    assert contributions.matched_total(session, movement.id) == 700


def test_dangling_parent_legacy_live_payment_remains_directly_matchable():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.persistence.models import Base

    # A separate in-memory legacy fixture intentionally lacks FK enforcement;
    # the normal migrated test engine and its constraints remain unchanged.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            household = Household(name="Synthetic legacy")
            session.add(household)
            session.flush()
            account = Account(household_id=household.id, name="Synthetic", type="joint",
                              opening_date=DAY)
            session.add(account)
            session.flush()
            movement = ExpectedMovement(household_id=household.id, account_id=account.id,
                expected_date=DAY, amount_pence=1000, label="Synthetic", confidence="scheduled",
                source_type="manual", source_id=1)
            tx = Transaction(account_id=account.id, date=DAY, amount_pence=1000,
                payee="Synthetic", source="manual", corrects_id=999999)
            session.add_all([movement, tx])
            session.flush()
            contributions.confirm(session, movement, tx)
            assert contributions.matched_total(session, movement.id) == 1000
    finally:
        engine.dispose()


def test_cycle_validation_crosses_non_equivalent_account_edges(session, household, payment):
    _, movement, original = payment
    other = Account(household_id=household.id, name="Changed destination", type="joint",
                    opening_date=DAY)
    session.add(other)
    session.flush()
    original.status = TransactionStatus.reconciled
    current = transactions.correct(session, original, "Synthetic")
    current.account_id = movement.account_id = other.id
    original.corrects_id = current.id
    session.flush()
    with pytest.raises(contributions.MatchError, match="cycl"):
        contributions.confirm(session, movement, current)
    assert session.scalar(select(func.count()).select_from(ExpectedMatch)) == 0
    assert not session.new and not session.dirty and not session.deleted
