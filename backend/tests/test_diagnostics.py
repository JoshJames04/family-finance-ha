"""Issue #26 — sanitised diagnostics bundle.

ACs: the bundle contains no financial values or identifiers
(digit runs redacted); it is downloadable for issue reporting;
job + sync status surfaces are populated.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.main import create_app
from app.persistence.engine import create_app_engine
from app.persistence.models import (
    Account,
    BankConnection,
    BankProvider,
    ConnStatus,
    Household,
    NotificationDelivery,
    NotificationKind,
    NotificationRule,
    SyncCursor,
    SyncRun,
    SyncStatus,
    TransactionSource,
    UserProfile,
)


@pytest.fixture()
def pair(tmp_path: Path) -> Iterator[tuple[TestClient, Session]]:
    """One SQLite file shared between the TestClient app and a
    direct session — sensitive rows seed through the session while
    the API reads the same store."""
    url = f"sqlite:///{(tmp_path / 'shared.db').as_posix()}"
    with TestClient(create_app(Settings.load(
            {"FF_DATA_DIR": str(tmp_path), "FF_ENV": "ci",
             "FF_DATABASE_URL": url}))) as c:
        factory = sessionmaker(bind=create_app_engine(url))
        with factory() as s:
            yield c, s


@pytest.fixture()
def member(pair) -> tuple[TestClient, Session, UserProfile]:
    client, session = pair
    hh = Household(name="H")
    session.add(hh)
    session.flush()
    p = UserProfile(household_id=hh.id, display_name="Admin",
                    role="admin")
    session.add(p)
    session.commit()
    return client, session, p


def _seed_sensitive(session: Session, hh_id: int) -> None:
    a = Account(household_id=hh_id, name="Current 1234",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    c = SyncCursor(account_id=a.id, source=TransactionSource.starling)
    c.status = SyncStatus.failed
    c.error = ("HTTP 429 from starling api — retry after 300s "
               "(acct 87654321)")
    session.add(c)
    session.add(SyncRun(
        account_id=a.id, source=TransactionSource.starling,
        status=SyncStatus.failed,
        error="feed failed after 42 items, balance was 123456p",
        finished_at=datetime.now()))
    session.add(BankConnection(
        household_id=hh_id, provider=BankProvider.starling,
        vault_ref="token-998877", status=ConnStatus.degraded,
        last_error="token for account 112233 rejected"))
    rule = NotificationRule(household_id=hh_id, name="r",
                            kind=NotificationKind.spend_above,
                            params={"threshold_pence": 5000})
    session.add(rule)
    session.flush()
    session.add(NotificationDelivery(
        household_id=hh_id, rule_id=rule.id, channel="ha_notify",
        event_count=1, status="failed",
        error="notify.mobile_app_pixel9 returned 502 after 3 tries"))
    session.commit()


_ISO_TS = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?")


def _digit_runs(node: Any) -> list[str]:
    """Collect digit runs inside string values — ISO timestamps are
    structural (last-good time is the feature), not identifiers."""
    out: list[str] = []
    if isinstance(node, str):
        if not _ISO_TS.match(node):
            out += re.findall(r"\d+", node)
    elif isinstance(node, dict):
        for v in node.values():
            out += _digit_runs(v)
    elif isinstance(node, list):
        for v in node:
            out += _digit_runs(v)
    return out


def test_bundle_shape(member) -> None:
    client, _, p = member
    body = client.get("/api/v1/diagnostics",
                      headers={"X-Profile-Id": str(p.id)})
    assert body.status_code == 200
    d = body.json()
    assert d["app"]["version"]
    assert d["database"]["alembic_revision"]
    assert d["database"]["table_rows"]["transaction"] >= 0
    assert d["scheduler"]["sync_interval_minutes"] > 0
    assert d["scheduler"]["tick_seconds"] > 0
    for k in ("sync", "bank_connections", "recent_failures"):
        assert k in d


def test_no_financial_values_or_identifiers(member) -> None:
    """Seed deliberately sensitive text; assert zero digit runs
    survive into string values (except structural fields like the
    version, which is code metadata, not financial data)."""
    client, session, p = member
    _seed_sensitive(session, p.household_id)
    body = client.get("/api/v1/diagnostics",
                      headers={"X-Profile-Id": str(p.id)})
    assert body.status_code == 200
    d = body.json()
    assert d["sync"] and d["sync"][0]["status"] == "failed"
    for leaked in ("87654321", "123456", "998877", "112233"):
        assert leaked not in body.text
    runs = _digit_runs(
        {k: v for k, v in d.items()
         if k not in ("app", "database")})
    assert runs == [], f"digit runs leaked: {runs[:5]}"
    assert d["sync"][0]["error"] and \
        "#" in d["sync"][0]["error"]


def test_downloadable_as_json(member) -> None:
    client, _, p = member
    r = client.get("/api/v1/diagnostics",
                   headers={"X-Profile-Id": str(p.id)})
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()


