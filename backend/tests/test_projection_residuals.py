"""Forecast only outstanding money; received money already belongs to the ledger."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import projection, scenario
from app.main import create_app
from app.persistence.models import (
    Account,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Household,
    ScenarioOpKind,
    Transaction,
    UserProfile,
)

TODAY = date(2025, 4, 10)


@pytest.fixture()
def account(session, household):
    account = Account(household_id=household.id, name="Synthetic", type="personal",
                      opening_date=TODAY, opening_balance_pence=0)
    session.add(account)
    session.flush()
    session.add(Transaction(account_id=account.id, date=TODAY, amount_pence=100000,
                            payee="Synthetic opening", status="cleared"))
    session.flush()
    return account


def matched(session, account, *, received, amount=10000, due=TODAY + timedelta(days=5),
            source=ExpectedSource.employment, today=TODAY):
    movement = ExpectedMovement(household_id=account.household_id, account_id=account.id,
        expected_date=due, amount_pence=amount, label="Synthetic expectation",
        confidence="scheduled", source_type=source, source_id=due.toordinal())
    receipt = Transaction(account_id=account.id, date=today - timedelta(days=1),
                          amount_pence=received, payee="Synthetic receipt", status="cleared")
    session.add_all([movement, receipt])
    session.flush()
    session.add(ExpectedMatch(movement_id=movement.id, transaction_id=receipt.id,
                              matched_pence=abs(received), manual=True))
    session.flush()
    return movement, receipt


@pytest.mark.parametrize("source", [ExpectedSource.employment, ExpectedSource.payroll_run])
@pytest.mark.parametrize("received,residual,end", [(4000, 6000, 110000),
                                                   (10000, 0, 110000),
                                                   (12000, 0, 112000)])
def test_early_salary_is_not_received_twice(session, account, source, received, residual, end):
    movement, receipt = matched(session, account, received=received, source=source)
    result = projection.project(session, account, 7, from_date=TODAY)
    assert sum(point.movements_pence for point in result.days) == residual
    assert result.days[-1].balance_pence == end
    assert result.next_payday == (date(2025, 4, 15) if residual else None)
    assert movement.amount_pence == 10000 and receipt.amount_pence == received


@pytest.mark.parametrize("received,residual,end", [(-4000, 6000, 90000),
                                                   (-10000, 0, 90000),
                                                   (-12000, 0, 88000)])
def test_paid_liabilities_reduce_remaining_commitments(session, account, received, residual, end):
    matched(session, account, received=received, amount=-10000,
            source=ExpectedSource.commitment)
    result = projection.project(session, account, 7, from_date=TODAY)
    assert result.committed_until_payday_pence == residual
    assert result.safe_to_spend_pence == end
    assert result.days[-1].balance_pence == end


def test_satisfied_and_dismissed_paydays_advance_without_writes(session, account):
    first, _ = matched(session, account, received=10000)
    dismissed, _ = matched(session, account, received=1000, due=date(2025, 4, 16))
    dismissed.dismissed_at = datetime(2025, 4, 10)
    last, _ = matched(session, account, received=4000, due=date(2025, 4, 17))
    what_if = scenario.create_scenario(session, account.household_id, "Synthetic")
    scenario.add_op(session, what_if, kind=ScenarioOpKind.shift,
                    expected_movement_id=last.id, expected_date=date(2025, 4, 18))
    session.flush()
    writes = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", observe)
    try:
        result = projection.project(session, account, 10, from_date=TODAY)
        shifted = scenario.run(session, what_if, account, 10, from_date=TODAY)
        assert result.next_payday == date(2025, 4, 17)
        assert result.days[-1].balance_pence == 121000
        assert shifted.baseline == result
        assert [(p.date, p.delta_pence) for p in shifted.diff.paydays] == [
            (date(2025, 4, 17), -6000)]
        assert shifted.projection.days[-1].balance_pence == 121000
        assert writes == [] and not session.dirty and not session.new
        assert first.amount_pence == dismissed.amount_pence == last.amount_pence == 10000
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", observe)


def test_projection_api_agrees_with_budget_and_preserves_private_scope(tmp_path):
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"})
    with TestClient(create_app(settings)) as client:
        today = date.today()
        due = today + timedelta(days=5)
        with Session(client.app.state.engine) as session:
            household = Household(name="Synthetic")
            session.add(household)
            session.flush()
            member = UserProfile(household_id=household.id, display_name="Member", role="member")
            other = UserProfile(household_id=household.id, display_name="Other", role="member")
            session.add_all([member, other])
            session.flush()
            accounts = [Account(household_id=household.id, name=f"Synthetic {owner}",
                                type="personal",
                                owner_profile_id=owner, opening_date=today)
                        for owner in [member.id, other.id, None]]
            session.add_all(accounts)
            session.flush()
            for account in accounts:
                matched(session, account, received=4000, due=due, today=today)
            session.commit()
            account_ids = [a.id for a in accounts]
            household_id, member_id, other_id = household.id, member.id, other.id
        client.headers.update({"X-Household-Id": str(household_id),
                               "X-Profile-Id": str(member_id)})
        budget = client.post("/api/v1/budgets", json={
            "name": "Synthetic plan", "owner_profile_id": member_id, "period_type": "custom",
            "start_date": (today - timedelta(days=1)).isoformat(), "end_date": due.isoformat(),
        })
        assert budget.status_code == 201, budget.text
        expected = client.get("/api/v1/expected-movements", params={
            "start": today.isoformat(), "end": due.isoformat(), "budget_id": budget.json()["id"],
        })
        assert expected.status_code == 200, expected.text
        assert len(expected.json()) == 1
        assert expected.json()[0]["outstanding_pence"] == 6000
        result = client.get(f"/api/v1/accounts/{account_ids[0]}/projection?days=7")
        assert result.status_code == 200, result.text
        assert result.json()["start_balance_pence"] == 4000
        assert result.json()["days"][-1]["balance_pence"] == 10000
        assert sum(point["movements_pence"] for point in result.json()["days"]) == 6000
        actuals = client.get(f"/api/v1/budgets/{budget.json()['id']}/actuals")
        assert actuals.status_code == 200, actuals.text
        assert actuals.json()["income_received_pence"] == 4000
        assert client.get(f"/api/v1/accounts/{account_ids[1]}/projection").status_code == 404
        assert client.get(f"/api/v1/accounts/{account_ids[2]}/projection").status_code == 200
        client.headers["X-Profile-Id"] = str(other_id)
        assert client.get(f"/api/v1/accounts/{account_ids[0]}/projection").status_code == 404
        with Session(client.app.state.engine) as session:
            assert list(session.scalars(select(ExpectedMovement.amount_pence))) == [10000] * 3
            assert list(session.scalars(select(ExpectedMatch.matched_pence))) == [4000] * 3
