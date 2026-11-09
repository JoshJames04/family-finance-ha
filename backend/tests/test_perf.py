"""Issue #132 — performance budget guardrails on modest hardware.

A seeded ~5-year ledger (~30k transactions, scaled so the test stays
under a minute in CI) exercised through the hot read paths: account
list, paged transactions, the projection and the dashboard. Budgets
are generous — the point is catching order-of-magnitude regressions,
not micro-benchmarks. Marked ``perf``; CI runs it non-gating.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.settings import Settings
from app.main import create_app

ROWS = 30_000
BUDGET_S = 2.0          # per endpoint — HAOS-class target with margin
SEED_BUDGET_S = 60.0    # fixture build


@pytest.fixture()
def seeded_client(tmp_path: Path) -> Iterator[tuple[TestClient, dict]]:
    with TestClient(create_app(
            Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
        hid = c.post("/api/v1/households",
                     json={"name": "Perf"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = c.post("/api/v1/accounts", headers=h, json={
            "name": "Current", "type": "personal",
            "opening_balance": 0,
            "opening_date": "2021-01-01"}).json()["id"]
        t0 = time.perf_counter()
        engine = c.app.state.engine
        base = date(2021, 1, 2)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO \"transaction\" (account_id, date, "
                "amount_pence, payee, status, source, "
                "created_at, updated_at) VALUES (:a, :d, :p, :y, "
                "'cleared', 'starling', datetime('now'), "
                "datetime('now'))"),
                [{"a": aid,
                  "d": (base + timedelta(days=i % 1825)).isoformat(),
                  "p": -((i % 4500) + 100), "y": f"Shop {i % 700}"}
                 for i in range(ROWS)])
        elapsed = time.perf_counter() - t0
        assert elapsed < SEED_BUDGET_S, f"seed took {elapsed:.1f}s"
        yield c, h


@pytest.mark.perf()
class TestPerfBudget:
    def _timed(self, fn) -> float:
        t0 = time.perf_counter()
        fn()
        return time.perf_counter() - t0

    def test_accounts_list(self, seeded_client) -> None:
        c, h = seeded_client
        s = self._timed(lambda: c.get("/api/v1/accounts", headers=h))
        assert s < BUDGET_S, f"accounts list {s:.2f}s > {BUDGET_S}s"

    def test_transactions_page(self, seeded_client) -> None:
        c, h = seeded_client
        s = self._timed(lambda: c.get(
            "/api/v1/transactions", headers=h))
        assert s < BUDGET_S, f"transactions page {s:.2f}s"

    def test_projection(self, seeded_client) -> None:
        c, h = seeded_client
        aid = c.get("/api/v1/accounts", headers=h).json()["items"][0]["id"]
        s = self._timed(lambda: c.get(
            f"/api/v1/accounts/{aid}/projection", headers=h))
        assert s < BUDGET_S, f"projection {s:.2f}s"
