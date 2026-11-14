"""#490: real entry-point regressions, using synthetic providers and databases only."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1 import banks as bankapi
from app.api.v1.settings import _status
from app.core import scheduler
from app.core.settings import Settings
from app.domain import discovery, sync
from app.domain.accounts import current_balance
from app.domain.banks import ValidatorUnavailable
from app.integrations import starling
from app.persistence.models import (
    Account,
    ConnStatus,
    Household,
    SyncRun,
    SyncStatus,
    Transaction,
    TransactionSource,
    TransactionStatus,
)
from tests.test_banking_live142 import linked

SRC = TransactionSource.starling


def request(**env):
    settings = Settings.load({"FF_DATA_DIR": "/tmp/fix490-unused", **env})
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=settings, scheduler_enabled=settings.scheduler_tick_seconds > 0)))


class Client:
    def __init__(self):
        self.calls = []

    def accounts(self, token):
        self.calls.append("discovery")
        return [discovery.DiscoveredAccount("1", "Account 1", category_uid="category-1")]

    def balance(self, *args):
        self.calls.append("balance")
        return 9000

    def feed_items(self, *args):
        self.calls.append("feed")
        return [sync.FeedItem("new", date.today(), -500, "Synthetic new")]


def manual(s, household_id, connection_id, client):
    return bankapi.sync_transactions(connection_id, s.get(Household, household_id),
                                     s, "synthetic", None, client, request())


@pytest.mark.parametrize("trigger", ["manual", "scheduled"])
def test_active_manual_sync_queues_concurrent_trigger_without_provider_calls(
        session, household, monkeypatch, trigger):
    account, conn, _ = linked(session, household)
    cursor = sync.cursor_for(session, account.id, SRC)
    cursor.refresh_requested = True
    session.commit()
    aid, hid, cid = account.id, household.id, conn.id
    factory = sessionmaker(bind=session.get_bind())
    entered, release = threading.Event(), threading.Event()
    errors = []

    class Blocking(Client):
        def feed_items(self, *args):
            entered.set()
            assert release.wait(15)
            return super().feed_items(*args)

    monkeypatch.setattr(bankapi.banks, "token", lambda *_: "synthetic")

    def first():
        try:
            with factory() as s:
                manual(s, hid, cid, Blocking())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(10)
    client = Client()
    try:
        with factory() as s:
            if trigger == "manual":
                result = manual(s, hid, cid, client)
                assert [run["status"] for run in result["runs"]] == ["queued"]
            else:
                assert scheduler.sync_tick(s, client=client, token_for=lambda *_: "synthetic") == 0
            s.commit()
            assert client.calls == []
            assert s.scalar(select(func.count()).select_from(SyncRun)) == 1
    finally:
        release.set()
        thread.join(15)
    assert not thread.is_alive() and errors == []
    session.expire_all()
    cursor = sync.cursor_for(session, aid, SRC)
    assert cursor.refresh_requested
    assert scheduler.sync_tick(session, client=client, token_for=lambda *_: "synthetic") == 1
    assert client.calls.count("feed") == 1
    assert session.scalar(select(func.count()).select_from(Transaction).where(
        Transaction.account_id == aid, Transaction.external_ref == "new")) == 1


@pytest.mark.parametrize("manual_run", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_opening_repair_waits_for_complete_feed_and_is_stable_on_first_success(
        session, household, monkeypatch, manual_run, failed):
    account, conn, _ = linked(session, household)
    account.opening_date = date.today() - timedelta(days=1)
    account.opening_balance_pence = 10000
    opening = Transaction(account_id=account.id, date=account.opening_date,
        amount_pence=10000, payee="Opening balance", status=TransactionStatus.reconciled,
        source=TransactionSource.system, external_ref=f"open:{account.id}")
    protected = Transaction(account_id=account.id, date=date.today() - timedelta(days=2),
        amount_pence=-500, payee="Synthetic protected", status=TransactionStatus.reconciled,
        source=SRC, external_ref="prior")
    session.add_all([opening, protected])
    session.commit()
    monkeypatch.setattr(bankapi.banks, "token", lambda *_: "synthetic")

    class Feed(Client):
        def feed_items(self, *args):
            if failed:
                raise ValidatorUnavailable("synthetic DNS failure")
            return super().feed_items(*args)

    def run():
        if manual_run:
            return manual(session, household.id, conn.id, Feed())
        return scheduler.sync_tick(session, client=Feed(), token_for=lambda *_: "synthetic",
                                   interval_seconds=0)

    run()
    assert protected.amount_pence == -500 and protected.payee == "Synthetic protected"
    assert protected.status is TransactionStatus.reconciled
    if failed:
        assert opening.amount_pence == 10000
        assert current_balance(session, account.id) == 9500
    else:
        assert current_balance(session, account.id) == 9000
        first_opening = opening.amount_pence
        run()
        assert current_balance(session, account.id) == 9000
        assert opening.amount_pence == first_opening


@pytest.mark.parametrize("failure", [ValidatorUnavailable("synthetic DNS failure"),
                                      starling.UpstreamError(503)])
def test_transient_retry_persists_and_keeps_unaffected_feed_eligible(
        session, household, failure):
    account, conn, _ = linked(session, household)
    second, _, mapping = linked(session, household, "2")
    mapping.connection_id = conn.id
    session.commit()

    class Feed(Client):
        def accounts(self, token):
            return [discovery.DiscoveredAccount(str(i), f"Account {i}",
                category_uid=f"category-{i}") for i in (1, 2)]

        def feed_items(self, token, uid, *args):
            self.calls.append(uid)
            if uid == "1":
                raise failure
            return []

    client = Feed()
    now = datetime.now()
    scheduler.sync_tick(session, client=client, token_for=lambda *_: "synthetic", now=now)
    cursor = sync.cursor_for(session, account.id, SRC)
    assert cursor.status is SyncStatus.failed
    assert cursor.retry_at is not None and cursor.retry_at > now + timedelta(seconds=60)
    assert sync.cursor_for(session, second.id, SRC).status is SyncStatus.completed
    session.commit()
    # A fresh session models restart; the failed feed must remain deferred.
    factory = sessionmaker(bind=session.get_bind())
    with factory() as fresh:
        scheduler.sync_tick(fresh, client=client, token_for=lambda *_: "synthetic", now=now)
    assert client.calls.count("1") == 1
    assert conn.status is ConnStatus.degraded


@pytest.mark.parametrize("tick", ["0", "60"])
def test_disabled_scheduler_has_no_next_run_even_with_connected_mapping(
        session, household, tick):
    linked(session, household)
    session.commit()
    req = request(FF_SCHEDULER_TICK_SECONDS=tick)
    req.app.state.scheduler_enabled = False
    status = _status(req, session, household.id)
    assert status.connections[0].next_due_at is None
    assert status.scheduler_enabled is False


def test_process_exit_releases_owner_and_stale_running_cursor_can_resume(
        session, household, tmp_path):
    account, conn, _ = linked(session, household)
    session.commit()
    ready = tmp_path / "ready"
    code = '''
import sys, time
from pathlib import Path
from sqlalchemy.orm import Session
from app.persistence.engine import create_app_engine
from app.persistence.models import Account, TransactionSource
from app.domain import sync
engine = create_app_engine(sys.argv[1])
with Session(engine) as s:
    def blocked(cursor):
        Path(sys.argv[3]).write_text('ready')
        time.sleep(30)
        return iter(())
    sync.sync_account(s, s.get(Account, int(sys.argv[2])), TransactionSource.starling, blocked)
'''
    proc = subprocess.Popen([sys.executable, "-c", code, str(session.get_bind().url),
                             str(account.id), str(ready)])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        with Session(session.get_bind()) as other, pytest.raises(sync.SyncBusy):
            sync.sync_account(other, other.get(Account, account.id), SRC, lambda _: iter(()))
    finally:
        proc.kill()
        proc.wait(timeout=5)
    session.expire_all()
    assert sync.cursor_for(session, account.id, SRC).status is SyncStatus.running
    assert scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic") == 1
    assert sync.cursor_for(session, account.id, SRC).status is SyncStatus.completed

@pytest.mark.parametrize("manual_run", [False, True])
def test_spaces_repair_uses_new_balance_after_feed_not_discovery_cache(
        session, household, monkeypatch, manual_run):
    account, conn, mapping = linked(session, household)
    mapping.external_kind = "space"
    account.opening_date = date.today() - timedelta(days=1)
    session.add(Transaction(account_id=account.id, date=date.today() - timedelta(days=2),
        amount_pence=-500, payee="Synthetic prior", status=TransactionStatus.reconciled,
        source=SRC, external_ref="prior"))
    session.commit()
    spaces_reads = []

    def get(token, path):
        if path.endswith("/accounts"):
            return {"accounts": [{"accountUid": "1", "defaultCategory": "main"}]}
        if path.endswith("/spaces"):
            spaces_reads.append(path)
            return {"savingsGoals": [{"savingsGoalUid": "category-1", "name": "Synthetic",
                "totalSaved": {"minorUnits": 9500 if len(spaces_reads) == 1 else 9000}}]}
        assert "/feed/" in path
        return {"feedItems": [{"feedItemUid": "new", "amount": {"minorUnits": 500},
            "direction": "OUT", "status": "SETTLED", "transactionTime": date.today().isoformat(),
            "counterPartyName": "Synthetic new"}]}

    monkeypatch.setattr(starling, "_get", get)
    monkeypatch.setattr(bankapi.banks, "token", lambda *_: "synthetic")
    client = starling.StarlingClient()
    if manual_run:
        manual(session, household.id, conn.id, client)
    else:
        scheduler.sync_tick(session, client=client, token_for=lambda *_: "synthetic")
    assert len(spaces_reads) == 2
    assert mapping.balance_pence == 9000
    assert current_balance(session, account.id) == 9000


@pytest.mark.parametrize("manual_run", [False, True])
def test_partial_page_failure_keeps_committed_checkpoint_without_opening_repair(
        session, household, monkeypatch, manual_run):
    account, conn, _ = linked(session, household)
    account.opening_date = date.today() - timedelta(days=1)
    opening = Transaction(account_id=account.id, date=account.opening_date,
        amount_pence=10000, payee="Opening balance", status=TransactionStatus.reconciled,
        source=TransactionSource.system, external_ref=f"open:{account.id}")
    session.add_all([opening, Transaction(account_id=account.id,
        date=date.today() - timedelta(days=2), amount_pence=-500, payee="Synthetic protected",
        status=TransactionStatus.reconciled, source=SRC, external_ref="prior")])
    session.commit()

    def fetch(cursor):
        yield sync.FeedPage([sync.FeedItem("partial", date.today(), -500, "Synthetic partial")],
                            cursor_ref="committed-page")
        raise sync.SyncError("synthetic incomplete feed")

    monkeypatch.setattr(bankapi.banks, "token", lambda *_: "synthetic")
    monkeypatch.setattr(bankapi, "starling_fetch", lambda *args, **kwargs: fetch)
    monkeypatch.setattr(scheduler, "_starling_fetch", lambda *args, **kwargs: fetch)
    if manual_run:
        manual(session, household.id, conn.id, Client())
    else:
        scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic")
    cursor = sync.cursor_for(session, account.id, SRC)
    assert cursor.status is SyncStatus.failed
    assert cursor.last_ref == "committed-page"
    assert opening.amount_pence == 10000
    assert current_balance(session, account.id) == 9000


def test_ownership_is_scoped_by_database_even_with_same_account_id(session, household):
    from sqlalchemy import create_engine

    from app.domain.sync_lock import claim

    other_engine = create_engine("sqlite:///:memory:")
    try:
        with Session(other_engine) as other, claim(session, "account:1:starling") as first:
            assert first
            with claim(other, "account:1:starling") as second:
                assert second
    finally:
        other_engine.dispose()

@pytest.mark.parametrize("manual_run", [False, True])
def test_transient_retry_after_takes_precedence_and_recovery_clears_it(
        session, household, monkeypatch, manual_run):
    account, conn, _ = linked(session, household)
    session.commit()
    monkeypatch.setattr(bankapi.banks, "token", lambda *_: "synthetic")

    class Failing(Client):
        def feed_items(self, *args):
            raise starling.UpstreamError(503, retry_after=900)

    now = datetime.now()
    if manual_run:
        manual(session, household.id, conn.id, Failing())
    else:
        scheduler.sync_tick(session, client=Failing(), token_for=lambda *_: "synthetic")
    cursor = sync.cursor_for(session, account.id, SRC)
    assert cursor.retry_at >= now + timedelta(seconds=890)
    cursor.retry_at = now - timedelta(seconds=1)
    session.commit()
    if manual_run:
        manual(session, household.id, conn.id, Client())
    else:
        scheduler.sync_tick(session, client=Client(), token_for=lambda *_: "synthetic")
    assert cursor.retry_at is None and cursor.error is None
    assert conn.status is ConnStatus.connected and conn.last_error is None


@pytest.mark.parametrize("delay", [float("inf"), float("nan"), -1.0, 1e300])
def test_invalid_rate_limit_delay_has_a_finite_future_deadline(session, household, delay):
    account, _, _ = linked(session, household)

    def limited(cursor):
        raise sync.RateLimited("synthetic limit", retry_after=delay)
        yield

    now = datetime.now()
    run = sync.sync_account(session, account, SRC, limited)
    assert run.status is SyncStatus.failed
    cursor = sync.cursor_for(session, account.id, SRC)
    assert now + timedelta(seconds=50) <= cursor.retry_at <= now + timedelta(seconds=70)

@pytest.mark.parametrize("manual_run", [False, True])
def test_post_feed_spaces_permission_loss_is_not_reported_as_recovered(
        session, household, monkeypatch, manual_run):
    account, conn, mapping = linked(session, household)
    mapping.external_kind = "space"
    account.opening_date = date.today() - timedelta(days=1)
    session.add(Transaction(account_id=account.id, date=date.today() - timedelta(days=2),
        amount_pence=-500, payee="Synthetic prior", status=TransactionStatus.reconciled,
        source=SRC, external_ref="prior"))
    session.commit()
    spaces_reads = []

    def get(token, path):
        if path.endswith("/accounts"):
            return {"accounts": [{"accountUid": "1", "defaultCategory": "main"}]}
        if path.endswith("/spaces"):
            spaces_reads.append(path)
            if len(spaces_reads) == 2:
                raise starling.TokenHttpError(403)
            return {"savingsGoals": [{"savingsGoalUid": "category-1", "totalSaved": {
                "minorUnits": 9000}}]}
        return {"feedItems": []}

    monkeypatch.setattr(starling, "_get", get)
    monkeypatch.setattr(bankapi.banks, "token", lambda *_: "synthetic")
    client = starling.StarlingClient()
    if manual_run:
        manual(session, household.id, conn.id, client)
    else:
        scheduler.sync_tick(session, client=client, token_for=lambda *_: "synthetic")
    assert conn.status is ConnStatus.degraded
    assert "Spaces" in conn.last_error
    cursor = sync.cursor_for(session, account.id, SRC)
    assert cursor.status is SyncStatus.failed and cursor.retry_at is not None
    assert not session.scalar(select(Transaction).where(
        Transaction.external_ref == f"open:{account.id}"))

@pytest.mark.parametrize("payload", [{}, {"feedItems": {}}, {"feedItems": [None]},
    {"feedItems": [{"amount": {"minorUnits": 500}}]}])
def test_incomplete_provider_feed_does_not_advance_checkpoint_or_repair_opening(
        session, household, monkeypatch, payload):
    account, conn, _ = linked(session, household)
    account.opening_date = date.today() - timedelta(days=1)
    opening = Transaction(account_id=account.id, date=account.opening_date,
        amount_pence=10000, payee="Opening balance", status=TransactionStatus.reconciled,
        source=TransactionSource.system, external_ref=f"open:{account.id}")
    session.add_all([opening, Transaction(account_id=account.id,
        date=date.today() - timedelta(days=2), amount_pence=-500, payee="Synthetic prior",
        status=TransactionStatus.reconciled, source=SRC, external_ref="prior")])
    cursor = sync.cursor_for(session, account.id, SRC)
    cursor.last_ref = "2026-01-01T00:00:00Z"
    session.commit()

    def get(token, path):
        if path.endswith("/accounts"):
            return {"accounts": [{"accountUid": "1", "defaultCategory": "category-1"}]}
        if path.endswith("/spaces"):
            return {"savingsGoals": []}
        if path.endswith("/balance"):
            return {"effectiveBalance": {"minorUnits": 9000}}
        return payload

    monkeypatch.setattr(starling, "_get", get)
    scheduler.sync_tick(session, client=starling.StarlingClient(), token_for=lambda *_: "synthetic")
    assert cursor.status is SyncStatus.failed
    assert cursor.last_ref == "2026-01-01T00:00:00Z"
    assert cursor.retry_at is not None
    assert opening.amount_pence == 10000

def test_balance_only_refresh_does_not_rewrite_opening_before_feed(
        session, household, monkeypatch):
    account, conn, _ = linked(session, household)
    account.opening_date = date.today() - timedelta(days=1)
    opening = Transaction(account_id=account.id, date=account.opening_date,
        amount_pence=10000, payee="Opening balance", status=TransactionStatus.reconciled,
        source=TransactionSource.system, external_ref=f"open:{account.id}")
    session.add_all([opening, Transaction(account_id=account.id,
        date=date.today() - timedelta(days=2), amount_pence=-500, payee="Synthetic prior",
        status=TransactionStatus.reconciled, source=SRC, external_ref="prior")])
    session.commit()
    monkeypatch.setattr(bankapi.banks, "token", lambda *_: "synthetic")
    result = bankapi.sync_balances(conn.id, household, session, "synthetic", None, Client())
    assert result == {"synced": 1}
    assert opening.amount_pence == 10000
    assert current_balance(session, account.id) == 9500

@pytest.mark.parametrize("manual_run", [False, True])
def test_huge_finite_provider_retry_does_not_escape_error_callback(
        session, household, monkeypatch, manual_run):
    account, conn, _ = linked(session, household)
    session.commit()
    monkeypatch.setattr(bankapi.banks, "token", lambda *_: "synthetic")

    class Limited(Client):
        def feed_items(self, *args):
            raise starling.UpstreamError(429, retry_after=1e300)

    now = datetime.now()
    if manual_run:
        manual(session, household.id, conn.id, Limited())
    else:
        scheduler.sync_tick(session, client=Limited(), token_for=lambda *_: "synthetic")
    cursor = sync.cursor_for(session, account.id, SRC)
    assert cursor.status is SyncStatus.failed and cursor.last_ref is None
    assert conn.status is ConnStatus.degraded
    assert now + timedelta(seconds=50) <= cursor.retry_at <= now + timedelta(seconds=70)
