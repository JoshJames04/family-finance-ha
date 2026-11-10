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
from app.domain import discovery as discdom
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
    """feed_items + the discovery/balance surface the tick calls —
    scheduled sync refreshes provider state per due connection
    (#416). ``accounts`` must echo the linked mapping back or
    discovery legitimately marks it closed."""

    def __init__(self, items=None, *, accounts=None, balance=0):
        self._items = items or []
        self._accounts = accounts if accounts is not None else [
            discdom.DiscoveredAccount(
                "ext-1", "Current", category_uid="cat-1")]
        self._balance = balance
        self.calls = 0
        self.balance_calls = 0

    def accounts(self, token):
        return list(self._accounts)

    def balance(self, token, uid, cat=None):
        self.balance_calls += 1
        return self._balance

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


def test_sync_tick_refreshes_balances_and_names(
        session: Session, household: Household,
        account: Account) -> None:
    """#416: a due connection gets provider discovery + balance
    refresh on the scheduled tick — previously manual /sync only.
    Rename propagates to an account still carrying the provider
    label; the balance lands on the mapping."""
    m = _link(session, household, account)
    client = _FakeClient(
        accounts=[discdom.DiscoveredAccount(
            "ext-1", "Renamed", category_uid="cat-1")],
        balance=12_345)
    n = scheduler.sync_tick(
        session, client=client, token_for=lambda s, conn: "tok",
        now=NOW)
    assert n == 1
    assert client.balance_calls == 1
    assert m.balance_pence == 12_345 and m.balance_at is not None
    assert account.name == "Renamed"


def test_sync_tick_discovers_once_per_connection(
        session: Session, household: Household,
        account: Account) -> None:
    """Two due mappings on one connection → one balance/discovery
    round, not one per mapping."""
    a2 = Account(household_id=household.id, name="Space",
                 type="personal", opening_balance_pence=0,
                 opening_date=date(2025, 1, 1))
    session.add(a2)
    session.flush()
    m = _link(session, household, account)
    m2 = BankAccountMapping(
        household_id=household.id, connection_id=m.connection_id,
        external_uid="ext-1", external_label="Space",
        external_kind="space", status=MappingStatus.linked,
        account_id=a2.id, external_category_uid="cat-2")
    session.add(m2)
    session.flush()
    client = _FakeClient(accounts=[
        discdom.DiscoveredAccount(
            "ext-1", "Current", category_uid="cat-1"),
        discdom.DiscoveredAccount(
            "ext-1", "Space", kind="space", category_uid="cat-2")])
    n = scheduler.sync_tick(
        session, client=client, token_for=lambda s, conn: "tok",
        now=NOW)
    assert n == 2
    assert client.balance_calls == 2      # one per linked mapping
    assert client.calls == 2              # one feed per mapping


def test_sync_tick_balance_failure_still_syncs_feed(
        session: Session, household: Household,
        account: Account) -> None:
    """A provider error on the refresh marks the connection but the
    transaction pull still runs — a flaky balance endpoint must not
    starve transaction sync."""
    m = _link(session, household, account)

    class FlakyClient(_FakeClient):
        def balance(self, token, uid, cat=None):
            raise RuntimeError("provider 500")

    client = FlakyClient([syncdom.FeedItem(
        "s1", date(2025, 5, 30), -100, "T")])
    n = scheduler.sync_tick(
        session, client=client, token_for=lambda s, conn: "tok",
        now=NOW)
    assert n == 1
    conn = session.get(BankConnection, m.connection_id)
    assert conn is not None and conn.last_error is not None


def test_sync_tick_discovery_closed_skips_feed(
        session: Session, household: Household,
        account: Account) -> None:
    """Provider no longer lists the account → mapping closes and the
    feed pull is skipped in the same tick."""
    _link(session, household, account)
    client = _FakeClient(
        [syncdom.FeedItem("s1", date(2025, 5, 30), -100, "T")],
        accounts=[])                        # gone provider-side
    n = scheduler.sync_tick(
        session, client=client, token_for=lambda s, conn: "tok",
        now=NOW)
    assert n == 0 and client.calls == 0


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
