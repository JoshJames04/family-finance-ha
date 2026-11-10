"""Issue #431 — persisted in-app bank-sync interval.

AC: admin saves an interval in Settings → it persists across restarts
and applies to the running scheduler; an env/options override wins
and makes the control read-only; non-admin reads but never writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import appconfig
from app.main import create_app
from app.persistence.models import (
    Account,
    AccountType,
    AuditEvent,
    BankAccountMapping,
    BankConnection,
    BankProvider,
    ConnStatus,
    MappingStatus,
    SyncCursor,
    TransactionSource,
)

ADMIN = {"X-Admin-Token": "s3cret"}


def _app(tmp_path: Path, **env: str):
    return create_app(Settings.load(
        {"FF_DATA_DIR": str(tmp_path), "FF_ADMIN_TOKEN": "s3cret",
         **env}))


def _household(client: TestClient) -> int:
    return client.post("/api/v1/households",
                       json={"name": "H"}).json()["id"]


class TestBankingSettingsGet:
    def test_defaults(self, tmp_path: Path) -> None:
        with TestClient(_app(tmp_path)) as c:
            _household(c)
            r = c.get("/api/v1/settings/banking")
            assert r.status_code == 200
            body = r.json()
            assert body["interval_minutes"] == 240.0
            assert body["source"] == "default"
            assert body["editable"] is True
            assert body["min_minutes"] == 15.0
            assert body["max_minutes"] == 1440.0
            assert body["tick_seconds"] == 60.0
            assert body["connections"] == []

    def test_env_override_is_read_only(self, tmp_path: Path) -> None:
        with TestClient(_app(tmp_path, FF_SYNC_INTERVAL_MINUTES="30")
                        ) as c:
            _household(c)
            body = c.get("/api/v1/settings/banking").json()
            assert body["interval_minutes"] == 30.0
            assert body["source"] == "environment"
            assert body["editable"] is False


class TestBankingSettingsPatch:
    def test_admin_save_applies_and_audits(self, tmp_path: Path) -> None:
        with TestClient(_app(tmp_path)) as c:
            hid = _household(c)
            r = c.patch("/api/v1/settings/banking",
                        json={"sync_interval_minutes": 15},
                        headers=ADMIN)
            assert r.status_code == 200
            body = r.json()
            assert body["interval_minutes"] == 15.0
            assert body["source"] == "app"
            # Persisted, not just reported.
            with Session(c.app.state.engine) as s:
                assert appconfig.get_value(
                    s, appconfig.SYNC_INTERVAL_KEY) == "15.0"
                audit = s.scalar(select(AuditEvent).where(
                    AuditEvent.action == "settings.banking.update"))
                assert audit is not None
                assert audit.household_id == hid

    def test_persists_across_restart(self, tmp_path: Path) -> None:
        with TestClient(_app(tmp_path)) as c:
            _household(c)
            c.patch("/api/v1/settings/banking",
                    json={"sync_interval_minutes": 60}, headers=ADMIN)
        # A fresh app instance on the same data dir sees the value —
        # the scheduler resolves it on its next wake (#431 AC).
        with TestClient(_app(tmp_path)) as c2:
            body = c2.get("/api/v1/settings/banking").json()
            assert body["interval_minutes"] == 60.0
            assert body["source"] == "app"

    def test_bounds_enforced(self, tmp_path: Path) -> None:
        with TestClient(_app(tmp_path)) as c:
            _household(c)
            for bad in (0, 5, 14, 1441, 10000):
                assert c.patch("/api/v1/settings/banking",
                               json={"sync_interval_minutes": bad},
                               headers=ADMIN).status_code == 422

    def test_non_admin_cannot_write(self, tmp_path: Path) -> None:
        with TestClient(_app(tmp_path)) as c:
            hid = _household(c)
            member = c.post("/api/v1/profiles",
                            json={"display_name": "Kid",
                                  "role": "member"},
                            headers={"X-Household-Id": str(hid)}).json()
            assert c.patch("/api/v1/settings/banking",
                           json={"sync_interval_minutes": 15}).status_code == 403
            assert c.patch(
                "/api/v1/settings/banking",
                json={"sync_interval_minutes": 15},
                headers={"X-Profile-Id": str(member["id"])}
            ).status_code == 403

    def test_env_override_rejects_patch(self, tmp_path: Path) -> None:
        with TestClient(_app(tmp_path, FF_SYNC_INTERVAL_MINUTES="30")
                        ) as c:
            _household(c)
            r = c.patch("/api/v1/settings/banking",
                        json={"sync_interval_minutes": 15},
                        headers=ADMIN)
            assert r.status_code == 409


class TestConnectionStatus:
    def test_linked_mapping_reports_sync_state(
            self, tmp_path: Path) -> None:
        with TestClient(_app(tmp_path)) as c:
            hid = _household(c)
            with Session(c.app.state.engine) as s:
                conn = BankConnection(
                    household_id=hid, provider=BankProvider.starling,
                    vault_ref="v://x", status=ConnStatus.connected,
                    scopes="x")
                acct = Account(
                    household_id=hid, name="Joint",
                    type=AccountType.joint,
                    opening_balance_pence=0,
                    opening_date=datetime(2026, 1, 1).date(),
                    currency="GBP")
                s.add_all([conn, acct])
                s.flush()
                mapping = BankAccountMapping(
                    household_id=hid, connection_id=conn.id,
                    external_uid="ext-1", external_label="Joint",
                    external_kind="default",
                    external_category_uid="cat-1",
                    account_id=acct.id, status=MappingStatus.linked)
                s.add(mapping)
                s.flush()
                cursor = SyncCursor(
                    account_id=acct.id,
                    source=TransactionSource.starling,
                    last_success_at=datetime.now() - timedelta(minutes=10))
                s.add(cursor)
                s.commit()
                conn_id, acct_id = conn.id, acct.id
                last_ok = cursor.last_success_at
            body = c.get("/api/v1/settings/banking").json()
            assert len(body["connections"]) == 1
            st = body["connections"][0]
            assert st["connection_id"] == conn_id
            assert st["account_id"] == acct_id
            assert st["status"] == "connected"
            assert st["last_success_at"] is not None
            assert st["refresh_queued"] is False
            # Next due = last success + effective interval.
            due = datetime.fromisoformat(st["next_due_at"])
            assert abs((due - (last_ok + timedelta(minutes=240)))
                       .total_seconds()) < 5


class TestDomainResolver:
    def test_invalid_stored_value_falls_back(self, session) -> None:
        s = Settings.load({"FF_DATA_DIR": "/tmp/x"})
        appconfig.set_value(session, appconfig.SYNC_INTERVAL_KEY,
                            "not-a-number")
        assert appconfig.effective_sync_interval_minutes(
            session, s) == (240.0, "default")
        appconfig.set_value(session, appconfig.SYNC_INTERVAL_KEY, "5")
        assert appconfig.effective_sync_interval_minutes(
            session, s) == (240.0, "default")
        appconfig.set_value(session, appconfig.SYNC_INTERVAL_KEY, "90")
        assert appconfig.effective_sync_interval_minutes(
            session, s) == (90.0, "app")
