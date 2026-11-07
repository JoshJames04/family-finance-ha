"""Issue #118 — condition-alert kinds: trigger→dispatch per type,
severity levels, once-per-condition dedup, optional all-clear."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import notifications as notify
from app.persistence.models import (
    Account,
    AnomalyFlag,
    AnomalyStatus,
    AnomalyTrigger,
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    Category,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    NotificationEvent,
    NotificationKind,
    PriceChangeEvent,
    Subscription,
    SubscriptionStatus,
    SyncCursor,
    SyncStatus,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

TODAY = date(2025, 4, 8)


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Current",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _rule(session: Session, hid: int, kind: NotificationKind,
          params: dict[str, object] | None = None):
    return notify.create_rule(session, hid, name=kind.value, kind=kind,
                              params=params or {})


def _events(session: Session):
    return session.scalars(
        select(NotificationEvent).order_by(NotificationEvent.id)).all()


class TestUnusualTransaction:
    def test_pending_flag_alerts_once(self, session: Session,
                                      household: Household,
                                      account: Account) -> None:
        tx = Transaction(
            account_id=account.id, date=TODAY, amount_pence=-99_999,
            payee="odd", status=TransactionStatus.cleared,
            source="manual")
        session.add(tx)
        session.flush()
        session.add(AnomalyFlag(
            household_id=household.id, account_id=account.id,
            transaction_id=tx.id, trigger=AnomalyTrigger.amount_outlier,
            status=AnomalyStatus.pending, detail={}))
        session.flush()
        rule = _rule(session, household.id,
                     NotificationKind.unusual_transaction)
        assert notify.evaluate(session, rule, today=TODAY) == 1
        assert _events(session)[0].payload["severity"] == "info"
        assert notify.evaluate(session, rule, today=TODAY) == 0


class TestPriceChange:
    def test_change_event_alerts_once(self, session: Session,
                                      household: Household) -> None:
        sub = Subscription(
            household_id=household.id, payee="StreamCo",
            payee_normalised="streamco", frequency=Frequency.monthly,
            expected_pence=1_000, next_renewal_date=TODAY, anchor_day=8,
            status=SubscriptionStatus.active)
        session.add(sub)
        session.flush()
        session.add(PriceChangeEvent(
            household_id=household.id, subscription_id=sub.id,
            detected_date=TODAY, old_pence=1_000, new_pence=1_499))
        session.flush()
        rule = _rule(session, household.id,
                     NotificationKind.price_change)
        assert notify.evaluate(session, rule, today=TODAY) == 1
        ev = _events(session)[0]
        assert ev.payload["delta_pence"] == 499
        assert ev.payload["severity"] == "warning"
        assert notify.evaluate(session, rule, today=TODAY) == 0


class TestMissedTransfer:
    def _movement(self, session: Session, hid: int, aid: int,
                  on: date) -> None:
        session.add(ExpectedMovement(
            household_id=hid, account_id=aid, expected_date=on,
            amount_pence=-20_000, label="Bills top-up",
            confidence=Confidence.scheduled,
            source_type=ExpectedSource.transfer_rule, source_id=3))
        session.flush()

    def test_overdue_alerts_critical(self, session: Session,
                                     household: Household,
                                     account: Account) -> None:
        self._movement(session, household.id, account.id,
                       TODAY.replace(day=5))
        rule = _rule(session, household.id,
                     NotificationKind.missed_transfer)
        assert notify.evaluate(session, rule, today=TODAY) == 1
        assert _events(session)[0].payload["severity"] == "critical"

    def test_grace_period_suppresses(self, session: Session,
                                     household: Household,
                                     account: Account) -> None:
        self._movement(session, household.id, account.id, TODAY)
        rule = _rule(session, household.id,
                     NotificationKind.missed_transfer,
                     {"grace_days": 1})
        assert notify.evaluate(session, rule, today=TODAY) == 0


class TestSyncFailure:
    def test_failed_cursor_alerts_and_clears(
            self, session: Session, household: Household,
            account: Account) -> None:
        cursor = SyncCursor(
            account_id=account.id, source=TransactionSource.starling,
            status=SyncStatus.failed)
        session.add(cursor)
        session.flush()
        rule = _rule(session, household.id,
                     NotificationKind.sync_failure,
                     {"all_clear": True})
        assert notify.evaluate(session, rule, today=TODAY) == 1
        assert _events(session)[0].payload["severity"] == "critical"
        # Recovery → one all-clear event; then quiet.
        cursor.status = SyncStatus.completed
        session.flush()
        assert notify.evaluate(session, rule, today=TODAY) == 1
        assert _events(session)[1].dedup_key.endswith(":clear")
        assert notify.evaluate(session, rule, today=TODAY) == 0


class TestBudgetOverspend:
    def test_overspent_line_alerts(self, session: Session,
                                   household: Household,
                                   account: Account) -> None:
        cat = Category(household_id=household.id, name="Food")
        session.add(cat)
        session.flush()
        b = Budget(household_id=household.id, name="April",
                   period_type=BudgetPeriodType.monthly,
                   start_date=date(2025, 4, 1),
                   end_date=date(2025, 4, 30),
                   status=BudgetStatus.approved)
        session.add(b)
        session.flush()
        session.add(BudgetLine(
            budget_id=b.id, category_id=cat.id,
            side=BudgetLineSide.expense, original_pence=10_000))
        session.add(Transaction(
            account_id=account.id, date=TODAY, amount_pence=-12_000,
            payee="grocer", category_id=cat.id,
            status=TransactionStatus.cleared, source="manual"))
        session.flush()
        rule = _rule(session, household.id,
                     NotificationKind.budget_overspend)
        assert notify.evaluate(session, rule, today=TODAY) == 1
        ev = _events(session)[0]
        assert ev.payload["spent_pence"] == 12_000
        assert ev.payload["severity"] == "warning"
        assert notify.evaluate(session, rule, today=TODAY) == 0


class TestLowProjected:
    def test_below_threshold_alerts_warning(
            self, session: Session, household: Household,
            account: Account) -> None:
        session.add(ExpectedMovement(
            household_id=household.id, account_id=account.id,
            expected_date=TODAY.replace(day=10), amount_pence=-50_000,
            label="big bill", confidence=Confidence.scheduled,
            source_type=ExpectedSource.commitment, source_id=2))
        session.flush()
        rule = _rule(session, household.id,
                     NotificationKind.low_projected_balance,
                     {"threshold_pence": 0, "days": 30})
        assert notify.evaluate(session, rule, today=TODAY) == 1
        assert _events(session)[0].payload["severity"] == "warning"
        # Sustained condition is once-per-episode, not per poll.
        assert notify.evaluate(session, rule, today=TODAY) == 0
