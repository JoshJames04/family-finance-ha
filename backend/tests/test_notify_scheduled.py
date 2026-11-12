"""Issue #117 — scheduled notification kinds: weekly digest content,
payday reminder, bill alerts N days ahead.

AC: bills due Thu + Sat, today Tue with N=3 → Thursday's is raised,
Saturday's is not.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import notifications as notify
from app.persistence.models import (
    Account,
    Confidence,
    Employment,
    ExpectedMovement,
    ExpectedSource,
    Household,
    NotificationEvent,
    NotificationKind,
    Transaction,
    TransactionStatus,
    UserProfile,
)

TUE = date(2025, 4, 8)          # Tuesday
THU = TUE.replace(day=10)       # +2
SAT = TUE.replace(day=12)       # +4


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Current",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _rule(session: Session, hid: int, kind: NotificationKind,
          params: dict[str, object]):
    return notify.create_rule(session, hid, name=kind.value, kind=kind,
                              params=params)


def _movement(session: Session, hid: int, aid: int, on: date,
              pence: int, label: str,
              src: ExpectedSource = ExpectedSource.commitment,
              sid: int = 1) -> ExpectedMovement:
    m = ExpectedMovement(
        household_id=hid, account_id=aid, expected_date=on,
        amount_pence=pence, label=label,
        confidence=Confidence.scheduled, source_type=src, source_id=sid)
    session.add(m)
    session.flush()
    return m


class TestBillReminder:
    def test_window_includes_thursday_not_saturday(
            self, session: Session, household: Household,
            account: Account) -> None:
        """AC: Tue, N=3 → Thu (due in 2d) raised, Sat (in 4d) not."""
        rule = _rule(session, household.id,
                     NotificationKind.bill_reminder,
                     {"days_before": 3})
        _movement(session, household.id, account.id, THU, -5_000,
                  "Council tax")
        _movement(session, household.id, account.id, SAT, -7_500,
                  "Car payment")
        assert notify.evaluate(session, rule, today=TUE) == 1
        ev = session.scalars(select(NotificationEvent)).one()
        assert ev.payload["label"] == "Council tax"

    def test_dedup_once_per_bill(self, session: Session,
                               household: Household,
                               account: Account) -> None:
        rule = _rule(session, household.id,
                     NotificationKind.bill_reminder,
                     {"days_before": 3})
        _movement(session, household.id, account.id, THU, -5_000,
                  "Council tax")
        assert notify.evaluate(session, rule, today=TUE) == 1
        assert notify.evaluate(session, rule,
                               today=TUE.replace(day=9)) == 0


class TestPaydayReminder:
    def test_fires_days_before(self, session: Session,
                               household: Household,
                               account: Account) -> None:
        rule = _rule(session, household.id,
                     NotificationKind.payday_reminder,
                     {"days_before": 1})
        owner = UserProfile(household_id=household.id, display_name="Salary owner")
        session.add(owner)
        session.flush()
        employment = Employment(household_id=household.id, profile_id=owner.id,
                                employer="Synthetic", jurisdiction="england_wales",
                                contracted_hours=37)
        session.add(employment)
        session.flush()
        rule.target_profile_id = owner.id
        _movement(session, household.id, account.id,
                  TUE.replace(day=9), 250_000, "Salary",
                  src=ExpectedSource.employment, sid=employment.id)
        assert notify.evaluate(session, rule, today=TUE) == 1
        assert notify.evaluate(session, rule,
                               today=TUE.replace(day=9)) == 0


class TestWeeklyDigest:
    def test_fires_only_on_configured_weekday(
            self, session: Session, household: Household,
            account: Account) -> None:
        rule = _rule(session, household.id,
                     NotificationKind.weekly_digest, {"weekday": 1})
        assert notify.evaluate(session, rule, today=TUE) == 1
        assert notify.evaluate(session, rule,
                               today=TUE.replace(day=9)) == 0

    def test_content_composition(self, session: Session,
                                 household: Household,
                                 account: Account) -> None:
        """Digest carries 7-day spend + upcoming liabilities."""
        session.add(Transaction(
            account_id=account.id, date=TUE, amount_pence=-12_000,
            payee="shop", status=TransactionStatus.cleared,
            source="manual"))
        _movement(session, household.id, account.id, THU, -5_000,
                  "Council tax")
        session.flush()
        rule = _rule(session, household.id,
                     NotificationKind.weekly_digest, {"weekday": 1})
        assert notify.evaluate(session, rule, today=TUE) == 1
        ev = session.scalars(select(NotificationEvent)).one()
        assert ev.payload["spent_pence"] == 12_000
        assert ev.payload["liabilities_pence"] == 5_000
        assert ev.dedup_key == "digest:2025-W15"

    def test_once_per_week(self, session: Session,
                           household: Household) -> None:
        rule = _rule(session, household.id,
                     NotificationKind.weekly_digest, {"weekday": 1})
        assert notify.evaluate(session, rule, today=TUE) == 1
        # Same ISO week on the same weekday can't re-fire; a week
        # later is a new dedup key.
        assert notify.evaluate(session, rule, today=TUE) == 0
        assert notify.evaluate(
            session, rule, today=TUE.replace(day=15)) == 1
