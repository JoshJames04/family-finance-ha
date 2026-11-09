"""Issue #31 — append-only audit trail.

DB triggers make UPDATE/DELETE abort; a static walk proves every
mutation route emits; payload scrubbing keeps raw amounts out of the
log.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.audit import emit
from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import AuditEvent, Household


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def event(session: Session) -> AuditEvent:
    h = Household(name="H")
    session.add(h)
    session.flush()
    e = emit(session, h.id, "tester", "create", "account", 1,
             {"amount_pence": 123456, "note": "opening"})
    session.commit()  # durable — trigger-abort rollbacks must keep it
    return e


class TestAppendOnlyTriggers:
    def test_update_aborts(self, session: Session,
                           event: AuditEvent) -> None:
        with pytest.raises(DBAPIError):
            session.execute(
                text("UPDATE audit_event SET action = 'x' "
                     "WHERE id = :i"), {"i": event.id})
        session.rollback()
        assert session.get(AuditEvent, event.id).action == "create"

    def test_delete_aborts(self, session: Session,
                           event: AuditEvent) -> None:
        with pytest.raises(DBAPIError):
            session.execute(
                text("DELETE FROM audit_event WHERE id = :i"),
                {"i": event.id})
        session.rollback()
        assert session.get(AuditEvent, event.id) is not None

    def test_insert_still_allowed(self, session: Session) -> None:
        h = Household(name="H2")
        session.add(h)
        session.flush()
        emit(session, h.id, "a", "create", "x", 1)
        session.flush()
        assert session.query(AuditEvent).count() == 1


class TestCoverage:
    def test_every_mutation_route_emits(self) -> None:
        """A mutation route with no emit call is a static failure —
        impossible to merge un-audited mutation paths (issue #31 AC)."""
        app = create_app()
        missing: list[str] = []
        for r in app.routes:
            methods = getattr(r, "methods", set()) or set()
            if not (methods & {"POST", "PATCH", "PUT", "DELETE"}):
                continue
            path = getattr(r, "path", "")
            if not path.startswith("/api/v1"):
                continue
            if "emit" not in inspect.getsource(r.endpoint):
                missing.append(f"{sorted(methods)} {path}")
        assert missing == []

    def test_get_routes_do_not_emit(self) -> None:
        app = create_app()
        for r in app.routes:
            methods = getattr(r, "methods", set()) or set()
            if methods == {"GET"} and getattr(
                    r, "path", "").startswith("/api/v1"):
                assert "emit(" not in inspect.getsource(r.endpoint)


class TestPayloadDigest:
    def test_amounts_hashed(self, session: Session,
                            event: AuditEvent) -> None:
        body = json.loads(event.payload or "{}")
        assert body["amount_pence"].startswith("sha256:")
        assert "123456" not in (event.payload or "")
        assert body["note"] == "opening"  # non-sensitive keys kept

    def test_same_amount_same_digest(self, session: Session) -> None:
        h = Household(name="H")
        session.add(h)
        session.flush()
        e1 = emit(session, h.id, "a", "u", "x", 1,
                  {"amount_pence": 500})
        e2 = emit(session, h.id, "a", "u", "x", 2,
                  {"amount_pence": 500})
        session.flush()
        p1 = json.loads(e1.payload or "{}")["amount_pence"]
        p2 = json.loads(e2.payload or "{}")["amount_pence"]
        assert p1 == p2  # digest comparison still works for recon

    def test_end_to_end_via_api(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        client.post("/api/v1/accounts", json={
            "name": "C", "type": "joint", "opening_balance": 0,
            "opening_date": "2026-01-01"}, headers=h)
        from app.persistence import session as ps
        assert ps._session_factory is not None
        with ps._session_factory() as s:
            ev = s.query(AuditEvent).filter_by(
                entity_type="account", action="create").one()
        assert ev.actor and ev.household_id == hid
