"""Regression proofs for post-review settlement and sync failures."""
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import sync
from app.main import create_app
from app.persistence.models import (
    Account,
    IntegrationEvent,
    SyncCursor,
    SyncRun,
    SyncStatus,
    Transaction,
    TransactionSource,
)


@pytest.mark.parametrize("phase", ["fetch", "ingest"])
def test_unexpected_sync_failure_finalizes_run_and_preserves_only_completed_pages(
        session, household, monkeypatch, phase):
    account = Account(
        household_id=household.id, name="Synthetic", type="personal",
        opening_balance_pence=0, opening_date=date(2025, 1, 1))
    session.add(account)
    session.flush()

    def item(ref):
        return sync.FeedItem(ref, date(2025, 4, 10), -1000, "Synthetic")

    def feed(cursor):
        yield sync.FeedPage([item("complete")], cursor_ref="checkpoint")
        if phase == "fetch":
            raise ValueError("malformed provider time")
        yield sync.FeedPage([item("incomplete"), item("bad")], cursor_ref="lost")

    original = sync._ingest_item

    def ingest(session, account, source, incoming, run):
        if incoming.external_ref == "bad":
            raise ValueError("malformed provider amount")
        return original(session, account, source, incoming, run)

    monkeypatch.setattr(sync, "_ingest_item", ingest)
    with pytest.raises(ValueError, match="malformed provider"):
        sync.sync_account(session, account, TransactionSource.starling, feed)
    # A separate connection observes durable state, not dirty identity-map fields.
    with Session(session.get_bind()) as persisted:
        run = persisted.scalar(select(SyncRun))
        cursor = persisted.scalar(select(SyncCursor))
        assert run.status == SyncStatus.failed
        assert run.finished_at is not None
        assert run.inserted == run.fetched == 1
        assert cursor.status == SyncStatus.failed
        assert cursor.error
        assert cursor.last_ref == "checkpoint"
        assert list(persisted.scalars(select(Transaction.external_ref))) == ["complete"]
        events = list(persisted.scalars(select(IntegrationEvent)))
        assert [event.event_type for event in events].count("sync.failed") == 1


@pytest.mark.parametrize("kind", ["unhandled", "explicit"])
def test_server_error_counter_counts_each_failed_response_once(tmp_path, kind):
    app = create_app(Settings.load({
        "FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"}))

    @app.get("/review-failure")
    def fail():
        if kind == "explicit":
            raise HTTPException(503, "synthetic unavailable")
        raise RuntimeError("synthetic unhandled failure")

    with TestClient(app, raise_server_exceptions=False) as client:
        baseline = app.state.telemetry.errors_5xx
        response = client.get("/review-failure")
        assert response.status_code == (503 if kind == "explicit" else 500)
        assert app.state.telemetry.errors_5xx - baseline == 1
