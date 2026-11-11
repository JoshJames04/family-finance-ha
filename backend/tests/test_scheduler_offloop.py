"""Issue #451 — scheduler + snapshot work must run off the uvicorn
event loop: a tick performing synchronous sync/notify I/O used to
stall every inbound request (seen through ingress as widget 503s).

The tick here blocks on a threading.Event mid-flight; the request
must still complete — on the old in-loop design it would hang until
the tick released.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    yield TestClient(create_app(Settings.load({
        "FF_DATA_DIR": str(tmp_path),
        "FF_SCHEDULER_TICK_SECONDS": "0.05"})))


def test_tick_runs_off_event_loop(client: TestClient,
                                  monkeypatch: pytest.MonkeyPatch
                                  ) -> None:
    in_tick = threading.Event()
    release = threading.Event()
    threads: list[threading.Thread] = []

    from app.core import scheduler

    def fake_sync_tick(*args: object, **kwargs: object) -> None:
        threads.append(threading.current_thread())
        in_tick.set()
        release.wait(timeout=5)

    def fake_notify_tick(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(scheduler, "sync_tick", fake_sync_tick)
    monkeypatch.setattr(scheduler, "notify_tick", fake_notify_tick)

    with client:
        assert in_tick.wait(timeout=10), "scheduler tick never fired"
        # The event loop must still answer while the tick is blocked.
        r = client.get("/api/v1/households")
        assert r.status_code in (200, 403, 404)
        release.set()
    assert threads, "sync_tick never ran"
    assert all(t is not threading.main_thread() for t in threads)
