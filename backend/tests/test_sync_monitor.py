"""Issue #70 — sync status monitoring + refresh queue + job runner.

ACs: failed sync surfaces cause + last-good; a manual refresh during
backoff is queued, not bypassed; scheduled sync runs through the job
runner tick; notification eval/dispatch/deliver run on the tick too.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import scheduler
from app.domain import sync as syncdom
from app.persistence.models import (
    Account,
    BankAccountMapping,
    BankConnection,
    BankProvider,
    ConnStatus,
    Household,
    MappingStatus,
    NotificationDelivery,
    NotificationKind,
    NotificationRule,
    SyncStatus,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

SRC = TransactionSource.starling
NOW = datetime(2025, 6, 1, 12, 0, 0)


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Current",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _link(session: Session, household: Household,
          account: Account) -> BankAccountMapping:
    """A connected connection + linked mapping (the scheduler's unit
    of work)."""
    conn = BankConnection(household_id=household.id,
                          provider=BankProvider.starling,
                          vault_ref="v1", status=ConnStatus.connected)
    session.add(conn)
    session.flush()
    m = BankAccountMapping(
        household_id=household.id, connection_id=conn.id,
        external_uid="ext-1", external_label="Current",
        status=MappingStatus.linked, account_id=account.id,
        external_category_uid="cat-1")
    session.add(m)
    session.flush()
    return m


class _FakeClient:
    """feed_items returning one item — the rest of the BankClient
    protocol is irrelevant to the tick."""

    def __init__(self, items=None):
        self._items = items or []
        self.calls = 0

    def feed_items(self, token, uid, cat, since):
        self.calls += 1
        return list(self._items)


def test_refresh_during_backoff_queues(session: Session,
                                       account: Account) -> None:
    """AC: refresh inside the retry window is queued, not run."""
    c = syncdom.cursor_for(session, account.id, SRC)
    c.retry_at = NOW + timedelta(minutes=5)
    session.flush()

    assert syncdom.request_refresh(
        session, account.id, SRC, now=NOW) is True
    assert c.refresh_requested is True
    # no run row minted — the request waits
    assert syncdom.drain_due(session, now=NOW) == []


def test_refresh_after_window_runs_now(session: Session,
                                       account: Account) -> None:
    c = syncdom.cursor_for(session, account.id, SRC)
    c.retry_at = NOW - timedelta(seconds=1)
    session.flush()
    assert syncdom.request_refresh(
        session, account.id, SRC, now=NOW) is False
    assert c.refresh_requested is False


def test_drain_due_respects_window(session: Session,
                                   account: Account) -> None:
    c = syncdom.cursor_for(session, account.id, SRC)
    c.refresh_requested = True
    c.retry_at = NOW + timedelta(minutes=1)   # still backing off
    session.flush()
    assert syncdom.drain_due(session, now=NOW) == []
    assert [x.id for x in syncdom.drain_due(
        session, now=NOW + timedelta(minutes=2))] == [c.id]


def test_success_clears_backoff_and_stamps_last_good(
        session: Session, account: Account) -> None:
    """AC: last-good time lands on the cursor; error clears."""
    c = syncdom.cursor_for(session, account.id, SRC)
    c.retry_at = NOW + timedelta(minutes=1)
    session.flush()
    run = syncdom.sync_account(
        session, account, SRC,
        lambda cursor: iter([syncdom.FeedPage(
            [syncdom.FeedItem("r1", date(2025, 5, 30), -500, "S")])]))
    assert run.status is SyncStatus.completed
    assert c.last_success_at is not None
    assert c.retry_at is None


def test_rate_limit_marks_backoff(session: Session,
                                  account: Account) -> None:
    """A RateLimited failure records the retry window + cause."""
    def boom(cursor):
        raise syncdom.RateLimited("429", retry_after=300)
        yield  # pragma: no cover — makes it a generator
    run = syncdom.sync_account(session, account, SRC, boom)
    c = syncdom.cursor_for(session, account.id, SRC)
    assert run.status is SyncStatus.failed
    assert c.error == "429"
    assert c.retry_at is not None and c.retry_at > datetime.now()


def test_sync_tick_runs_due_and_queued(session: Session,
                                       household: Household,
                                       account: Account) -> None:
    """The job runner drains a queued refresh AND a due account in
    one tick; a fresh cursor is skipped."""
    _link(session, household, account)
    c = syncdom.cursor_for(session, account.id, SRC)
    c.refresh_requested = True          # queued manual refresh
    session.flush()
    client = _FakeClient([syncdom.FeedItem(
        "s1", date(2025, 5, 30), -250, "T")])

    n = scheduler.sync_tick(
        session, client=client, token_for=lambda s, conn: "tok",
        interval_seconds=4 * 3600, now=NOW)
    assert n == 1
    assert client.calls == 1
    assert c.refresh_requested is False
    assert c.last_success_at is not None


def test_sync_tick_skips_fresh(session: Session, household: Household,
                              account: Account) -> None:
    _link(session, household, account)
    c = syncdom.cursor_for(session, account.id, SRC)
    c.last_success_at = NOW
    session.flush()
    client = _FakeClient()
    n = scheduler.sync_tick(
        session, client=client, token_for=lambda s, conn: "tok",
        interval_seconds=4 * 3600, now=NOW)
    assert n == 0 and client.calls == 0


def test_sync_tick_skips_revoked(session: Session,
                                household: Household,
                                account: Account) -> None:
    m = _link(session, household, account)
    conn = session.get(BankConnection, m.connection_id)
    assert conn is not None
    conn.status = ConnStatus.revoked
    session.flush()
    n = scheduler.sync_tick(
        session, client=_FakeClient(),
        token_for=lambda s, c: "tok", now=NOW)
    assert n == 0


def test_notify_tick_end_to_end(session: Session,
                                household: Household) -> None:
    """Rule evaluate → dispatch → deliver via injected notifier."""
    a = Account(household_id=household.id, name="Cur",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    session.add(Transaction(
        account_id=a.id, date=date.today(), amount_pence=-9000,
        payee="Big Shop", status=TransactionStatus.cleared,
        source=TransactionSource.manual))
    session.add(NotificationRule(
        household_id=household.id, name="big",
        kind=NotificationKind.spend_above,
        params={"threshold_pence": 5000}))
    session.flush()

    sent: list[tuple[str | None, str, str]] = []

    class N:
        def notify(self, service, title, message):
            sent.append((service, title, message))

    raised, delivered = scheduler.notify_tick(
        session, notifier=N(), now=datetime.now())
    assert raised >= 1 and delivered == 1
    assert sent and "Shop" in sent[0][2] or sent[0][1]
    d = session.scalar(select(NotificationDelivery))
    assert d is not None and d.status == "sent"
