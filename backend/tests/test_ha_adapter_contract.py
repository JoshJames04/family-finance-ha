"""Actual HA adapter against a synthetic FastAPI app; no mocked API responses."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from datetime import date, timedelta
from pathlib import Path

from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import (
    Account,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    GoalType,
    Household,
    SavingsGoal,
    SyncCursor,
    SyncStatus,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _adapter(monkeypatch):
    directory = Path(__file__).resolve().parents[2] / "custom_components" / "family_finance"
    package = types.ModuleType("_contract_ff_adapter")
    package.__path__ = [str(directory)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    # Only the unavailable HTTP transport dependency is substituted. Every
    # response below comes from the actual synthetic FastAPI app.
    transport = types.ModuleType("aiohttp")
    transport.ClientError = type("ClientError", (Exception,), {})
    transport.ClientTimeout = lambda **kwargs: None
    monkeypatch.setitem(sys.modules, "aiohttp", transport)
    for name in ["const", "api"]:
        spec = importlib.util.spec_from_file_location(
            f"{package.__name__}.{name}", directory / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
    return sys.modules[f"{package.__name__}.api"].FamilyFinanceClient


class Response:
    def __init__(self, response):
        self.response = response
        self.status = response.status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self):
        return self.response.json()


class Transport:
    def __init__(self, client):
        self.client = client
        self.paths = []

    def post(self, url, headers=None, json=None, **kwargs):
        return Response(self.client.post(url, headers=headers, json=json))

    def get(self, url, headers=None, **kwargs):
        self.paths.append(url)
        return Response(self.client.get(url, headers=headers))


def test_actual_adapter_nonempty_summary_and_real_sync_state(tmp_path, monkeypatch):
    app = create_app(
        Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0"})
    )
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            household = Household(name="Synthetic")
            session.add(household)
            session.flush()
            hid = household.id
            account = Account(
                household_id=hid, name="Synthetic account", type="joint", opening_date=date.today()
            )
            session.add(account)
            session.flush()
            session.add_all(
                [
                    ExpectedMovement(
                        household_id=hid,
                        account_id=account.id,
                        expected_date=date.today(),
                        amount_pence=-1000,
                        label="Synthetic bill",
                        confidence=Confidence.scheduled,
                        source_type=ExpectedSource.manual,
                        source_id=1,
                    ),
                    ExpectedMovement(
                        household_id=hid,
                        account_id=account.id,
                        expected_date=date.today() + timedelta(days=2),
                        amount_pence=10000,
                        label="Synthetic income",
                        confidence=Confidence.estimated,
                        source_type=ExpectedSource.employment,
                        source_id=2,
                    ),
                    SavingsGoal(
                        household_id=hid,
                        account_id=account.id,
                        name="Synthetic goal",
                        goal_type=GoalType.general,
                        target_pence=10000,
                        start_date=date.today(),
                    ),
                    SyncCursor(
                        account_id=account.id,
                        source="starling",
                        status=SyncStatus.failed,
                        error="synthetic failure",
                    ),
                ]
            )
            session.commit()
        transport = Transport(client)
        adapter = _adapter(monkeypatch)(
            transport, {"url": "http://testserver", "household_id": hid}
        )
        summary = asyncio.run(adapter.async_fetch_summary())
        assert summary["upcoming_bills_count"] == 1
        assert summary["next_payday"] == (date.today() + timedelta(days=2)).isoformat()
        assert summary["goals_total"] == 1
        assert summary["sync_status"] == "failed"
        assert not any("/expected?" in path or "/goals" in path for path in transport.paths)
        day = date.today() - timedelta(days=1)
        first = asyncio.run(adapter.async_record_energy(day, 1250))
        again = asyncio.run(adapter.async_record_energy(day, 1250))
        assert first["id"] == again["id"]
        assert again["source"] == "ha_statistics"
        assert again["wh"] == 1250
        rows = client.get("/api/v1/energy/readings", headers={"X-Household-Id": str(hid)}).json()
        assert len(rows) == 1
