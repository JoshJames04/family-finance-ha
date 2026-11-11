"""Issue #460 — dashboard 503s under scheduler/DB contention.

Three regression proofs:

- ``auth_middleware`` must resolve identity off the event loop —
  a write-blocked profile resolve previously froze every in-flight
  request for up to ``busy_timeout``, surfacing through ingress as
  503 storms.
- ``sync_account`` must commit per page — a single uncommitted
  writer held across all provider I/O stalled every other writer.
- ``/diagnostics`` must expose the runtime counters (tick health,
  lock errors, request volume) that make the next incident
  diagnosable without a repro session.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import app.api.auth as auth
from app.core.settings import Settings
from app.domain import sync as sync_domain
from app.domain.sync import FeedItem, FeedPage
from app.main import create_app
from app.persistence.engine import create_app_engine
from app.persistence.migrate import upgrade_head
from app.persistence.models import (
    Account,
    Household,
    TransactionSource,
)


def _app(tmp_path: Path, **env: str):
    return create_app(Settings.load(
        {"FF_DATA_DIR": str(tmp_path), **env}))


class TestResolveOffLoop:
    def test_blocked_resolve_does_not_freeze_requests(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
            ) -> None:
        """A stalled ingress-identity resolve must not stall
        unrelated requests — the pre-#460 design ran the blocking
        resolve on the event loop, so one writer-blocked resolve
        froze every in-flight request."""
        monkeypatch.setattr(auth, "SUPERVISOR_IP", "testclient")
        in_resolve = threading.Event()
        release = threading.Event()
        resolve_threads: list[threading.Thread] = []

        def slow_resolve(_request) -> None:
            resolve_threads.append(threading.current_thread())
            in_resolve.set()
            release.wait(timeout=15)
            return None

        monkeypatch.setattr(
            auth, "_resolve_profile_id", slow_resolve)
        with TestClient(
                _app(tmp_path, FF_INGRESS="true")) as client:
            def headered() -> None:
                client.get("/api/v1/households", headers={
                    "X-Remote-User-Id": "ha-1"})

            t = threading.Thread(target=headered)
            t.start()
            assert in_resolve.wait(timeout=10), (
                "identity resolve never entered")
            start = time.monotonic()
            # A request carrying no auth material skips resolution
            # entirely — it must still be answered while the
            # headered request's resolve is blocked.
            r = client.get("/api/v1/households")
            elapsed = time.monotonic() - start
            release.set()
            t.join(timeout=10)
        assert r.status_code == 200
        assert elapsed < 5, (
            f"unrelated request stalled {elapsed:.1f}s behind a "
            "blocked identity resolve — resolution is back on the "
            "event loop")
        assert resolve_threads, "resolve never ran"
        assert all(th is not threading.main_thread()
                   for th in resolve_threads)


class TestSyncWriterWindow:
    def test_writer_released_between_pages(self, tmp_path: Path
                                           ) -> None:
        """Mid-feed, a second connection must be able to write.

        WAL permits one writer: before #460 ``sync_account`` flushed
        but never committed, holding the write transaction open
        across every provider HTTP call — any concurrent write
        (including the auth middleware's) queued behind it for the
        whole sync."""
        url = f"sqlite:///{(tmp_path / 'c.db').as_posix()}"
        upgrade_head(url)
        engine = create_app_engine(url)
        factory = sessionmaker(bind=engine)
        with factory() as session:
            hh = Household(name="H")
            session.add(hh)
            session.flush()
            account = Account(
                household_id=hh.id, name="Current", type="personal",
                opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
            session.add(account)
            session.commit()
            db_file = str(tmp_path / "c.db")
            probe_errors: list[Exception] = []

            def feed(_cursor):
                yield FeedPage([FeedItem(
                    "r1", date(2025, 4, 10), -1_000, "Shop")])
                # Between pages: simulate network latency while a
                # concurrent write arrives — a held writer would
                # raise 'database is locked' here.
                try:
                    conn = sqlite3.connect(db_file, timeout=0.5)
                    conn.isolation_level = None
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        "UPDATE household SET name = name")
                    conn.execute("COMMIT")
                    conn.close()
                except sqlite3.Error as exc:
                    probe_errors.append(exc)
                yield FeedPage([FeedItem(
                    "r2", date(2025, 4, 11), -2_000, "Shop")])

            run = sync_domain.sync_account(
                session, account, TransactionSource.starling, feed)
            assert run.inserted == 2
        assert probe_errors == [], (
            f"concurrent write locked out mid-sync: {probe_errors}")


class TestRuntimeTelemetry:
    def test_diagnostics_exposes_runtime_counters(
            self, tmp_path: Path) -> None:
        with TestClient(_app(
                tmp_path,
                FF_SCHEDULER_TICK_SECONDS="0.05")) as client:
            hid = client.post(
                "/api/v1/households",
                json={"name": "H"}).json()["id"]
            p = client.post(
                "/api/v1/profiles",
                json={"display_name": "Mum", "role": "admin"},
                headers={"X-Household-Id": str(hid)}).json()
            time.sleep(0.3)   # let at least one tick land
            r = client.get("/api/v1/diagnostics",
                           headers={"X-Profile-Id": str(p["id"])})
        assert r.status_code == 200
        runtime = r.json()["runtime"]
        assert runtime["ticks_total"] >= 1
        assert runtime["last_tick_ms"] is not None
        assert runtime["requests_total"] >= 1
        assert runtime["tick_errors"] == 0
        assert runtime["db_locked_total"] == 0
