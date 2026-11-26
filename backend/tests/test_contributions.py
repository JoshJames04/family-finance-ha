"""Issue #87 — expected transfer legs matched to real movements:
satisfied / partial / overdue states; auto + manual matching."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import budget_periods, contributions, schedule
from app.domain.contributions import ContributionState, MatchError
from app.main import create_app
from app.persistence.models import (
    Account,
    AppMeta,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    Transaction,
    TransactionStatus,
    Transfer,
    TransferRule,
    UserProfile,
)

TODAY = date(2025, 4, 10)


@pytest.fixture()
def pair(session: Session, household: Household
         ) -> tuple[Account, Account, TransferRule]:
    src = Account(household_id=household.id, name="Joint",
                  type="joint", opening_date=date(2025, 1, 1))
    dst = Account(household_id=household.id, name="Bills",
                  type="bills", opening_date=date(2025, 1, 1))
    session.add_all([src, dst])
    session.flush()
    r = TransferRule(
        household_id=household.id, name="Bills top-up",
        source_account_id=src.id, dest_account_id=dst.id,
        amount_spec='{"kind": "fixed", "pence": 30000}',
        frequency=Frequency.monthly, day_of_month=1,
        start_date=date(2025, 1, 1), active=True)
    session.add(r)
    session.flush()
    return src, dst, r


def _leg(session: Session, household: Household,
         account: Account, pence: int, on: date) -> Transaction:
    t = Transfer(household_id=household.id)
    session.add(t)
    session.flush()
    tx = Transaction(
        account_id=account.id, date=on, amount_pence=pence,
        payee="Move", transfer_id=t.id,
        status=TransactionStatus.cleared, source="manual")
    session.add(tx)
    session.flush()
    return tx


def _movement(session: Session, household: Household,
              account: Account, pence: int, on: date,
              rule_id: int) -> ExpectedMovement:
    m = ExpectedMovement(
        household_id=household.id, account_id=account.id,
        expected_date=on, amount_pence=pence,
        label="Bills top-up (in)", confidence=Confidence.scheduled,
        source_type=ExpectedSource.transfer_rule, source_id=rule_id)
    session.add(m)
    session.flush()
    return m


def test_shared_standing_uses_each_current_pay_cycle_and_separate_next_payment(
    session: Session, household: Household, pair: tuple[Account, Account, TransferRule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import expected
    from app.domain import budget_periods
    from app.persistence.models import UserProfile

    class Today(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 11, 25)

    monkeypatch.setattr(expected, "date", Today)
    source, pot, first_rule = pair
    josh = UserProfile(household_id=household.id, display_name="Josh")
    chloe = UserProfile(household_id=household.id, display_name="Chloe")
    session.add_all([josh, chloe])
    session.flush()
    source.owner_profile_id = josh.id
    second_source = Account(household_id=household.id, name="Personal",
                            type="personal", owner_profile_id=chloe.id,
                            opening_date=date(2026, 1, 1))
    session.add(second_source)
    session.flush()
    second_rule = TransferRule(
        household_id=household.id, name="Second share", source_account_id=second_source.id,
        dest_account_id=pot.id, amount_spec='{"kind":"fixed","pence":20000}',
        frequency=Frequency.monthly, day_of_month=11, start_date=date(2026, 1, 1),
    )
    session.add(second_rule)
    session.flush()
    budget_periods.set_day(session, household.id, josh.id, 28)
    budget_periods.set_day(session, household.id, chloe.id, 11)
    for rule, paid, upcoming, amount in [
        (first_rule, date(2026, 10, 28), date(2026, 11, 28), 30000),
        (second_rule, date(2026, 11, 11), date(2026, 12, 11), 20000),
    ]:
        movement = _movement(session, household, pot, amount, paid, rule.id)
        receipt = _leg(session, household, pot, amount, paid)
        if rule is first_rule:
            debit = Transaction(account_id=source.id, date=paid, amount_pence=-amount,
                                payee="Shared contribution", transfer_id=receipt.transfer_id)
            session.add(debit)
            session.flush()
            planned_debit = _movement(session, household, source, -amount, paid, rule.id)
            contributions.confirm(session, planned_debit, debit)
        else:
            contributions.confirm(session, movement, receipt)
        _movement(session, household, pot, amount, upcoming, rule.id)
    internal = TransferRule(
        household_id=household.id, name="Shared top-up", source_account_id=pot.id,
        dest_account_id=source.id, amount_spec='{"kind":"fixed","pence":999}',
        frequency=Frequency.monthly, day_of_month=25, start_date=date(2026, 1, 1),
    )
    session.add(internal)
    session.flush()
    _movement(session, household, source, 999, date(2026, 11, 25), internal.id)

    rows = {row.profile_id: row for row in expected.contribution_cycles(
        household, session, None, date(2026, 11, 1), date(2026, 11, 30), pot.id)}
    assert (rows[josh.id].start, rows[josh.id].end) == (
        date(2026, 10, 28), date(2026, 11, 26))
    assert (rows[chloe.id].start, rows[chloe.id].end) == (
        date(2026, 11, 11), date(2026, 12, 10))
    for user, amount, due in [
        (josh, 30000, date(2026, 11, 28)), (chloe, 20000, date(2026, 12, 11)),
    ]:
        row = rows[user.id]
        payment, = row.payments
        assert payment.matched_pence == payment.expected_pence == amount
        assert row.next_payment is not None
        assert row.next_payment.due_date == due
        assert row.next_payment.matched_pence == 0


@pytest.mark.parametrize("old_day,new_day", [(28, 17), (17, 28)])
def test_moving_payday_across_today_preserves_the_existing_unpaid_date(
    session: Session, household: Household, pair: tuple[Account, Account, TransferRule],
    monkeypatch: pytest.MonkeyPatch, old_day: int, new_day: int,
) -> None:
    source, _, rule = pair
    member = UserProfile(household_id=household.id, display_name="Test payer")
    session.add(member)
    session.flush()
    source.owner_profile_id = member.id
    rule.amount_spec = '{"kind":"fixed","pence":30000,"on_member_payday":true}'
    day = old_day

    def member_payday(_session, _household, _profile, month):
        return month.replace(day=day)

    monkeypatch.setattr(budget_periods, "member_payday", member_payday)
    today = date(2025, 4, 20)
    schedule.rematerialize_transfer_rule(
        session, rule, today=date(2025, 4, 10), horizon_days=60)
    day = new_day
    schedule.rematerialize_transfer_rule(session, rule, today=today, horizon_days=40)
    due = session.scalars(select(ExpectedMovement.expected_date).where(
        ExpectedMovement.source_type == ExpectedSource.transfer_rule,
        ExpectedMovement.source_id == rule.id,
    )).all()
    assert sorted(due) == [date(2025, 4, old_day)] * 2 + [date(2025, 5, new_day)] * 2


class TestStates:
    def test_satisfied_by_full_match(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        """AC: £300 rule due on the 1st; a £300 leg satisfies it."""
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        _leg(session, household, dst, 30_000, date(2025, 4, 2))
        assert contributions.auto_match(
            session, household.id, today=TODAY) == 1
        s = contributions.status(session, mv, TODAY)
        assert s.state is ContributionState.satisfied
        assert s.matched_pence == 30_000

    def test_partial_leaves_remainder(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        """AC: £200 vs £300 → partial with £100 outstanding."""
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        _leg(session, household, dst, 20_000, date(2025, 4, 1))
        contributions.auto_match(session, household.id, today=TODAY)
        s = contributions.status(session, mv, TODAY)
        assert s.state is ContributionState.partial
        assert s.outstanding_pence == 10_000

    def test_overdue_when_due_passed(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        s = contributions.status(session, mv, TODAY)
        assert s.state is ContributionState.overdue

    def test_pending_when_due_ahead(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       TODAY + timedelta(days=5), rule.id)
        s = contributions.status(session, mv, TODAY)
        assert s.state is ContributionState.pending


class TestAutoMatch:
    def test_window_edge(self, session: Session, household: Household,
                         pair: tuple[Account, Account, TransferRule]
                         ) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 8), rule.id)
        inside = _leg(session, household, dst, 30_000,
                      date(2025, 4, 15))  # +7d — inside default window
        outside = _leg(session, household, dst, 30_000,
                       date(2025, 4, 16))  # +8d — outside
        n = contributions.auto_match(session, household.id, today=TODAY)
        assert n == 1
        s = contributions.status(session, mv, TODAY)
        assert s.matched_pence == inside.amount_pence
        assert outside.id != inside.id  # sanity

    def test_two_paired_receipts_in_same_account_stay_with_their_source(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        src, dst, rule = pair
        due = date(2025, 4, 1)
        other = Account(household_id=household.id, name="Other payer",
                        type=src.type, opening_date=due)
        session.add(other)
        session.flush()
        mv = _movement(session, household, dst, 30000, due, rule.id)
        wrong = _leg(session, household, dst, 30000, due)
        right = _leg(session, household, dst, 20000, due + timedelta(days=1))
        session.add_all([
            Transaction(account_id=other.id, date=wrong.date, amount_pence=-30000,
                        payee="Wrong payer", transfer_id=wrong.transfer_id),
            Transaction(account_id=src.id, date=right.date, amount_pence=-20000,
                        payee="Expected payer", transfer_id=right.transfer_id),
        ])
        session.flush()
        assert contributions.auto_match(session, household.id, today=TODAY) == 1
        assert contributions.status(session, mv, TODAY).matched_pence == 20000

    def test_wrong_sign_never_matches(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        _leg(session, household, dst, -30_000, date(2025, 4, 1))
        assert contributions.auto_match(
            session, household.id, today=TODAY) == 0
        assert contributions.status(
            session, mv, TODAY).matched_pence == 0

    def test_leg_matches_only_one_movement(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        m1 = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        m2 = _movement(session, household, dst, 30_000,
                       date(2025, 4, 3), rule.id)
        _leg(session, household, dst, 30_000, date(2025, 4, 1))
        contributions.auto_match(session, household.id, today=TODAY)
        assert contributions.status(
            session, m1, TODAY).matched_pence == 30_000
        assert contributions.status(
            session, m2, TODAY).matched_pence == 0

    def test_idempotent(self, session: Session, household: Household,
                        pair: tuple[Account, Account, TransferRule]
                        ) -> None:
        _, dst, rule = pair
        _movement(session, household, dst, 30_000,
                  date(2025, 4, 1), rule.id)
        _leg(session, household, dst, 30_000, date(2025, 4, 1))
        n1 = contributions.auto_match(session, household.id,
                                      today=TODAY)
        n2 = contributions.auto_match(session, household.id,
                                      today=TODAY)
        assert (n1, n2) == (1, 0)


class TestManualConfirm:
    def test_direct_bills_space_receipt_consumes_each_scheduled_leg_once(
        self, session: Session, household: Household, pair: tuple[Account, Account, TransferRule],
    ) -> None:
        source, joint, rule = pair
        member = UserProfile(household_id=household.id, display_name="Test payer")
        bills = Account(household_id=household.id, name="Alternative bills space",
                        type="bills", opening_date=TODAY)
        session.add_all([member, bills])
        session.flush()
        source.owner_profile_id = member.id
        session.add(AppMeta(key=f"shared_split:{household.id}:{joint.id}",
                            value=json.dumps({"mode": "equal",
                                              "alternate_account_ids": [bills.id]})))
        incoming = _movement(session, household, joint, 30000, TODAY, rule.id)
        outgoing = _movement(session, household, source, -30000, TODAY, rule.id)
        receipt = _leg(session, household, bills, 20000, TODAY)
        debit = Transaction(account_id=source.id, date=TODAY, amount_pence=-20000,
                            payee="Direct contribution", transfer_id=receipt.transfer_id)
        session.add(debit)
        session.flush()

        contributions.confirm(session, incoming, receipt)
        assert contributions.matched_totals(session, [incoming.id, outgoing.id]) == {
            incoming.id: 20000, outgoing.id: 20000,
        }
        totals = contributions.shared_payment_totals(session, [incoming], {outgoing.id: incoming})
        assert totals == {incoming.id: 20000}
        with pytest.raises(MatchError):
            contributions.confirm(session, incoming, receipt)
        incoming.transfer_source_account_id = source.id
        incoming.transfer_dest_account_id = joint.id
        outgoing.transfer_source_account_id = source.id
        outgoing.transfer_dest_account_id = joint.id
        session.delete(rule)
        session.flush()
        assert contributions.matched_totals(session, [incoming.id])[incoming.id] == 20000
        debit.status = TransactionStatus.void
        session.flush()
        assert contributions.matched_totals(session, [incoming.id, outgoing.id]) == {
            incoming.id: 0, outgoing.id: 0,
        }
        receipt.status = TransactionStatus.void
        session.flush()
        assert contributions.matched_totals(session, [incoming.id, outgoing.id]) == {
            incoming.id: 0, outgoing.id: 0,
        }

    def test_confirm_records_match(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        tx = _leg(session, household, dst, 30_000,
                  date(2025, 5, 20))  # outside auto window
        m = contributions.confirm(session, mv, tx, today=TODAY)
        assert m.manual is True
        assert contributions.status(
            session, mv, TODAY).state is ContributionState.satisfied

    @pytest.mark.parametrize("unavailable", ["source", "destination", "dismissed"])
    def test_unavailable_transfer_cannot_take_new_evidence(
            self, session, household, pair, unavailable):
        src, dst, rule = pair
        due = date(2025, 4, 1)
        movement = _movement(session, household, dst, 30_000, due, rule.id)
        tx = _leg(session, household, dst, 30_000, due)
        if unavailable == "dismissed":
            movement.dismissed_at = datetime(2025, 4, 1)
        else:
            account = src if unavailable == "source" else dst
            account.archived = True
        session.flush()

        with pytest.raises(MatchError, match="no longer available"):
            contributions.confirm(session, movement, tx, today=due)
        assert contributions.auto_match(session, household.id, today=due) == 0
        assert contributions.status(session, movement, due).matched_pence == 0

    def test_confirm_rejects_wrong_account(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        src, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        tx = _leg(session, household, src, 30_000, date(2025, 4, 1))
        with pytest.raises(MatchError):
            contributions.confirm(session, mv, tx, today=TODAY)

    def test_confirm_rejects_double_use(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        m1 = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        m2 = _movement(session, household, dst, 30_000,
                       date(2025, 4, 2), rule.id)
        tx = _leg(session, household, dst, 30_000, date(2025, 4, 1))
        contributions.confirm(session, m1, tx, today=TODAY)
        with pytest.raises(MatchError, match="already matched"):
            contributions.confirm(session, m2, tx, today=TODAY)


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def test_guided_split_is_atomic_and_preserves_existing_payment_days(
        self, client: TestClient,
    ) -> None:
        base = "/api/v1"
        household = client.post(f"{base}/households", json={"name": "Test"}).json()["id"]
        headers = {"X-Household-Id": str(household)}

        def post(path, payload):
            response = client.post(f"{base}/{path}", headers=headers, json=payload)
            assert response.status_code == 201, response.text
            return response.json()["id"]

        def account(name, owner=None):
            return post("accounts", {"name": name, "type": "personal" if owner else "joint",
                                     "owner_profile_id": owner, "opening_balance": 0,
                                     "opening_date": "2026-01-01"})

        josh = post("profiles", {"display_name": "Josh"})
        chloe = post("profiles", {"display_name": "Chloe"})
        josh_account, chloe_account = account("Josh Main", josh), account("Chloe Main", chloe)
        pot = account("Joint Main")
        rule_id = post("transfer-rules", {
            "name": "Josh share", "source_account_id": josh_account, "dest_account_id": pot,
            "amount_spec": {"kind": "fixed", "pence": 10000}, "frequency": "monthly",
            "day_of_month": 28, "start_date": "2026-01-01",
        })
        route = f"{base}/transfer-rules/shared-split"
        body = {"account_id": pot, "target_pence": 30001, "shares": [
            {"profile_id": josh, "rule_id": rule_id, "pence": 18001},
            {"profile_id": chloe, "source_account_id": josh_account, "day_of_month": 11,
             "pence": 12000},
        ]}
        assert client.post(route, headers=headers, json=body).status_code == 422

        def rules():
            return client.get(f"{base}/transfer-rules", headers=headers).json()["items"]

        (old,) = rules()
        assert old["amount_spec"]["pence"] == 10000
        body["shares"][1]["source_account_id"] = chloe_account
        assert client.post(route, headers=headers, json=body).status_code == 204
        refreshed = {rule["source_account_id"]: rule for rule in rules()}
        assert refreshed[josh_account]["day_of_month"] == 28
        assert refreshed[chloe_account]["day_of_month"] == 11
        assert sum(rule["amount_spec"]["pence"] for rule in refreshed.values()) == 30001

        policy = {"mode": "fixed_remainder", "remainder_profile_id": josh}
        body.update(policy=policy)
        body["shares"][0]["pence"] = 0  # the server computes the remainder
        body["shares"][1]["rule_id"] = refreshed[chloe_account]["id"]
        response = client.post(route, headers=headers, json=body)
        assert response.status_code == 204, response.text
        saved = client.get(f"{route}/{pot}", headers=headers).json()["policy"]
        assert saved["remainder_profile_id"] == josh
        assert saved["mode"] == "fixed_remainder"
        assert saved["fixed_pence"] == {str(chloe): 12000}
        assert {r["source_account_id"]: r["amount_spec"]["pence"] for r in rules()} == {
            josh_account: 18001, chloe_account: 12000,
        }
        body["shares"][1]["pence"] = 40000
        assert client.post(route, headers=headers, json=body).status_code == 422

        body["shares"][1]["pence"] = 12000
        for share in body["shares"]:
            share.pop("rule_id", None)
        # Replaying an unchanged submission keeps the same schedule IDs.
        old_ids = {rule["id"] for rule in rules()}
        assert client.post(route, headers=headers, json=body).status_code == 204
        assert {rule["id"] for rule in rules()} == old_ids
        body["shares"][1]["pence"] = 13000
        assert client.post(route, headers=headers, json=body).status_code == 409
        assert {rule["amount_spec"]["pence"] for rule in rules()} == {12000, 18001}


    def test_new_shared_schedule_follows_early_payday(self, client: TestClient) -> None:
        base = "/api/v1"
        household = client.post(f"{base}/households", json={"name": "Test"}).json()["id"]
        headers = {"X-Household-Id": str(household)}
        member = client.post(f"{base}/profiles", headers=headers,
                             json={"display_name": "Member", "role": "admin"}).json()["id"]
        headers["X-Profile-Id"] = str(member)
        def account(name, owner=None):
            return client.post(f"{base}/accounts", headers=headers, json={
                "name": name, "type": "personal" if owner else "joint",
                "owner_profile_id": owner, "opening_balance": 0,
                "opening_date": "2026-01-01",
            }).json()["id"]
        pot, src = account("Shared"), account("Personal", member)
        assert client.put(f"{base}/budgets/pay-cycle-settings", headers=headers, json={
            "owner_profile_id": member, "payday_day": 28,
        }).status_code == 200
        route = f"{base}/transfer-rules/shared-split"
        setup = client.get(f"{route}/{pot}", headers=headers).json()
        assert client.post(route, headers=headers, json={
            "account_id": pot, "target_pence": 20000, "policy": {"mode": "equal"},
            "shares": [{"profile_id": member, "source_account_id": src,
                        "pence": 0, "on_member_payday": True}],
        }).status_code == 204
        rules = client.get(f"{base}/transfer-rules", headers=headers).json()["items"]
        assert rules[0]["start_date"] == date.today().isoformat()
        due = setup["next_paydays"][str(member)]
        assert date.fromisoformat(due).weekday() < 5
        rows = client.get(f"{base}/expected-movements", headers=headers, params={
            "start": due, "end": due,
        }).json()
        assert sorted(row["amount_pence"] for row in rows) == [-20000, 20000]
        incoming = next(row for row in rows if row["amount_pence"] > 0)
        tx = client.post(f"{base}/transactions", headers=headers, json={
            "account_id": pot, "date": due, "amount": 20000, "payee": "Confirmed receipt",
        }).json()
        assert client.post(f"{base}/expected-movements/{incoming['id']}/matches",
                           headers=headers, json={"transaction_id": tx["id"]}).status_code == 201


        assert client.put(f"{base}/budgets/pay-cycle-settings", headers=headers, json={
            "owner_profile_id": member, "payday_day": 17,
        }).status_code == 200
        new_due = client.get(f"{route}/{pot}", headers=headers).json()["next_paydays"][str(member)]
        nominal_month = date.fromisoformat(due).replace(day=1)
        last = (nominal_month.replace(day=28) + timedelta(days=4)).replace(day=1)
        cycle = client.get(f"{base}/expected-movements", headers=headers, params={
            "start": nominal_month.isoformat(), "end": (last - timedelta(days=1)).isoformat(),
        }).json()
        assert {row["expected_date"] for row in cycle} == {due}
        assert len(cycle) == 2

        # The rule already started at the first agreed date. Compare a future month
        # that the old and new schedules would both cover.
        after = max(date.fromisoformat(due), date.fromisoformat(new_due))
        first = (after.replace(day=28) + timedelta(days=4)).replace(day=1)
        future = client.get(f"{base}/expected-movements", headers=headers, params={
            "start": first.isoformat(), "end": first.replace(day=28).isoformat(),
        }).json()
        assert len(future) == 2
        assert {date.fromisoformat(row["expected_date"]).day for row in future} <= {15, 16, 17}

        edit = client.patch(f"{base}/transfer-rules/{rules[0]['id']}", headers=headers, json={
            "day_of_month": 20,
        })
        assert edit.status_code == 200
        assert "on_member_payday" not in edit.json()["amount_spec"]

    def test_match_and_status_flow(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        src = client.post("/api/v1/accounts", json={
            "name": "Joint", "type": "joint", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        dst = client.post("/api/v1/accounts", json={
            "name": "Bills", "type": "bills", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        client.post("/api/v1/transfer-rules", headers=h, json={
            "name": "Bills", "source_account_id": src,
            "dest_account_id": dst,
            "amount_spec": {"kind": "fixed", "pence": 30000},
            "frequency": "monthly", "day_of_month": 1,
            "start_date": "2025-01-01"})
        # a real transfer on the next due date (rules only
        # materialise future legs)
        today = date.today()
        y, m = (today.year, today.month + 1) if today.month < 12 \
            else (today.year + 1, 1)
        due = date(y, m, 1).isoformat()
        d = client.post("/api/v1/transactions", headers=h, json={
            "account_id": dst, "date": due,
            "amount": 30000, "payee": "Top up"})
        c = client.post("/api/v1/transactions", headers=h, json={
            "account_id": src, "date": due,
            "amount": -30000, "payee": "Top up"})
        tr = client.post("/api/v1/transfers", headers=h, json={
            "debit_transaction_id": c.json()["id"],
            "credit_transaction_id": d.json()["id"]})
        assert tr.status_code == 201, tr.json()
        n = client.post("/api/v1/expected-movements/match",
                        headers=h).json()["matched"]
        assert n >= 1
        rows = client.get("/api/v1/expected-movements/contributions",
                          headers=h, params={
                              "start": due, "end": due}).json()
        states = {r["state"] for r in rows}
        assert "satisfied" in states


class TestOutstanding:
    """Issue #88 — aging, requirement totals, write-off/reschedule."""

    def test_missed_due_appears_with_age(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        _movement(session, household, dst, 30_000,
                  date(2025, 4, 1), rule.id)
        items = contributions.outstanding(
            session, household.id, today=TODAY)
        assert len(items) == 1
        assert items[0].age_days == 9
        assert items[0].outstanding_pence == 30_000

    def test_satisfied_not_outstanding(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        _movement(session, household, dst, 30_000,
                  date(2025, 4, 1), rule.id)
        _leg(session, household, dst, 30_000, date(2025, 4, 1))
        contributions.auto_match(session, household.id, today=TODAY)
        assert contributions.outstanding(
            session, household.id, today=TODAY) == []

    def test_partial_still_outstanding_with_remainder(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        _movement(session, household, dst, 30_000,
                  date(2025, 4, 1), rule.id)
        _leg(session, household, dst, 20_000, date(2025, 4, 1))
        contributions.auto_match(session, household.id, today=TODAY)
        items = contributions.outstanding(
            session, household.id, today=TODAY)
        assert items[0].outstanding_pence == 10_000

    def test_requirements_reconcile(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        """AC: requirement totals = scheduled − satisfied."""
        _, dst, rule = pair
        _movement(session, household, dst, 30_000,
                  date(2025, 4, 1), rule.id)
        _movement(session, household, dst, 30_000,
                  date(2025, 5, 1), rule.id)
        _leg(session, household, dst, 12_000, date(2025, 4, 2))
        contributions.auto_match(session, household.id, today=TODAY)
        reqs = contributions.requirement_totals(
            session, household.id, start=date(2025, 4, 1),
            end=date(2025, 5, 31), today=TODAY)
        assert len(reqs) == 2
        apr = next(r for r in reqs
                   if r.due_date == date(2025, 4, 1))
        assert apr.scheduled_pence == 30_000
        assert apr.satisfied_pence == 12_000
        assert apr.outstanding_pence == apr.scheduled_pence \
            - apr.satisfied_pence

    def test_write_off_excludes_everywhere(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        from datetime import datetime
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        contributions.write_off(session, mv, when=datetime.now())
        assert contributions.outstanding(
            session, household.id, today=TODAY) == []
        assert contributions.requirement_totals(
            session, household.id, start=date(2025, 4, 1),
            end=date(2025, 4, 30)) == []
        with pytest.raises(MatchError, match="already"):
            contributions.write_off(session, mv, when=datetime.now())

    def test_reschedule_moves_due_date(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 1), rule.id)
        contributions.reschedule(session, mv, date(2025, 4, 15),
                                 today=TODAY)
        assert mv.expected_date == date(2025, 4, 15)
        items = contributions.outstanding(
            session, household.id, today=TODAY)
        assert items[0].age_days == 0 if items else True
        # still past-due at 15th? Apr 15 > today Apr 10 → not overdue
        assert contributions.outstanding(
            session, household.id, today=TODAY) == []

    def test_reschedule_rejects_backward_and_dismissed(
            self, session: Session, household: Household,
            pair: tuple[Account, Account, TransferRule]) -> None:
        from datetime import datetime
        _, dst, rule = pair
        mv = _movement(session, household, dst, 30_000,
                       date(2025, 4, 10), rule.id)
        with pytest.raises(MatchError, match="after"):
            contributions.reschedule(session, mv, date(2025, 4, 5),
                                     today=TODAY)
        contributions.write_off(session, mv, when=datetime.now())
        with pytest.raises(MatchError, match="written off"):
            contributions.reschedule(session, mv, date(2025, 5, 1),
                                     today=TODAY)


class TestOutstandingApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def test_write_off_and_requirements_endpoints(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        src = client.post("/api/v1/accounts", json={
            "name": "Joint", "type": "joint", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        dst = client.post("/api/v1/accounts", json={
            "name": "Bills", "type": "bills", "opening_balance": 0,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        client.post("/api/v1/transfer-rules", headers=h, json={
            "name": "Bills", "source_account_id": src,
            "dest_account_id": dst,
            "amount_spec": {"kind": "fixed", "pence": 30000},
            "frequency": "monthly", "day_of_month": 1,
            "start_date": "2025-01-01"})
        reqs = client.get("/api/v1/expected-movements/requirements",
                          headers=h, params={
                              "start": date.today().isoformat(),
                              "end": "2030-01-01"}).json()
        assert reqs
        assert all(r["outstanding_pence"] == r["scheduled_pence"]
                   for r in reqs)
        mid = reqs[0]["due_date"]
        # write off the first future movement
        moves = client.get("/api/v1/expected-movements", headers=h,
                           params={"start": mid,
                                   "end": "2030-01-01"}).json()
        in_leg = next(m for m in moves if m["amount_pence"] > 0)
        wo = client.post(
            f"/api/v1/expected-movements/{in_leg['id']}/write-off",
            headers=h)
        assert wo.status_code == 200
        after = client.get(
            "/api/v1/expected-movements/requirements",
            headers=h, params={"start": mid, "end": "2030-01-01"}
        ).json()
        assert len(after) < len(reqs)


    def test_receiving_accounts_can_be_saved_without_a_budget(self, client: TestClient) -> None:
        base = "/api/v1"
        household = client.post(f"{base}/households", json={"name": "Home"}).json()["id"]
        headers = {"X-Household-Id": str(household)}
        ids = []
        for name in ("Joint", "Bills"):
            response = client.post(f"{base}/accounts", headers=headers, json={
                "name": name, "type": "joint", "opening_date": date.today().isoformat(),
                "opening_balance": 0,
            })
            assert response.status_code == 201
            ids.append(response.json()["id"])
        setup = f"{base}/transfer-rules/shared-split/{ids[0]}"
        route = f"{setup}/receiving-accounts"
        assert client.patch(route, headers=headers, json=[ids[0]]).status_code == 422
        assert client.patch(route, headers=headers, json=[ids[1]]).status_code == 204
        policy = client.get(setup, headers=headers).json()["policy"]
        assert policy["mode"] == "amount" and policy["alternate_account_ids"] == [ids[1]]
