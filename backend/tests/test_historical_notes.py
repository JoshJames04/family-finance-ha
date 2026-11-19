"""Historical archive behavior uses only invented annotations."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import AppMeta

URL = "/api/v1/historical-notes"


@pytest.fixture()
def archive_client(tmp_path: Path):
    with TestClient(create_app(Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
        hid = c.post("/api/v1/households", json={"name": "Synthetic"}).json()["id"]
        pid = c.post("/api/v1/profiles", json={
            "display_name": "Owner", "role": "admin"}).json()["id"]
        c.headers.update({"X-Household-Id": str(hid), "X-Profile-Id": str(pid)})
        yield c


def manifest():
    return {"source_sha256": "a" * 64, "provenance_status": "retained_extraction_unverified",
            "notes": [{"source_key": "synthetic-one", "sheet_index": 0,
                       "sheet_name": "Example archive", "year": 2020,
                       "worksheet_part": "xl/worksheets/sheet1.xml", "cell_reference": "C9",
                       "thread_id": "thread-example", "timestamp": "2020-01-01T00:00:00Z",
                       "text": "Original\n" + "context 😀 " * 500}]}


def test_roundtrip_idempotent_and_guarded_rollback(archive_client):
    c = archive_client
    assert c.get(URL).json()["notes"] == []
    body = manifest()
    imported = c.put(URL, json=body)
    assert imported.status_code == 200
    assert imported.json()["notes"] == body["notes"]
    digest = imported.json()["digest"]
    assert c.put(URL, json=body).json()["digest"] == digest
    with Session(c.app.state.engine) as s:
        rows = list(s.scalars(select(AppMeta).where(AppMeta.key.like("historical:%"))))
        assert rows and all(len(r.value) <= 1024 for r in rows)
        count = len(rows)
    changed = manifest()
    changed["notes"][0]["text"] = "changed"
    assert c.put(URL, json=changed).status_code == 409
    assert c.delete(URL, params={"digest": "b" * 64}).status_code == 409
    assert len(c.get(URL).json()["notes"]) == 1
    assert c.delete(URL, params={"digest": digest}).status_code == 204
    assert c.get(URL).json()["notes"] == []
    with Session(c.app.state.engine) as s:
        assert len(list(s.scalars(select(AppMeta).where(AppMeta.key.like("historical:%"))))) == 0
    assert count > 1


def test_private_owner_scope_and_no_profile_denied(archive_client):
    c = archive_client
    assert c.put(URL, json=manifest()).status_code == 200
    other = c.post("/api/v1/profiles", json={"display_name": "Other", "role": "admin"}).json()["id"]
    c.headers["X-Profile-Id"] = str(other)
    assert c.get(URL).json()["notes"] == []
    c.headers.pop("X-Profile-Id")
    assert c.get(URL).status_code == 403
    assert c.put(URL, json=manifest()).status_code == 403


def test_household_scope_is_checked_before_read_or_write(archive_client):
    c = archive_client
    assert c.put(URL, json=manifest()).status_code == 200
    other_hid = c.post("/api/v1/households", json={"name": "Elsewhere"}).json()["id"]
    c.headers["X-Household-Id"] = str(other_hid)
    assert c.get(URL).status_code == 404
    assert c.put(URL, json=manifest()).status_code == 404
    assert c.delete(URL, params={"digest": "a" * 64}).status_code == 404


def test_member_cannot_use_archive(archive_client):
    c = archive_client
    pid = c.post("/api/v1/profiles", json={
        "display_name": "Member", "role": "member"}).json()["id"]
    c.headers["X-Profile-Id"] = str(pid)
    assert c.get(URL).status_code == 403
    assert c.put(URL, json=manifest()).status_code == 403


@pytest.mark.parametrize("change", ["duplicate", "empty", "extra", "verified"])
def test_invalid_manifest_is_atomic_and_errors_do_not_echo_text(archive_client, change):
    body = manifest()
    if change == "duplicate":
        body["notes"].append(body["notes"][0].copy())
    elif change == "empty":
        body["notes"][0]["text"] = ""
    elif change == "extra":
        body["notes"][0]["current_record_id"] = 123
    else:
        body["provenance_status"] = "verified"
    r = archive_client.put(URL, json=body)
    assert r.status_code == 422
    assert "Original" not in r.text
    assert archive_client.get(URL).json()["notes"] == []


def test_openapi_publishes_archive_input_without_changing_private_validation():
    spec = create_app().openapi()
    operation = spec["paths"][URL]["put"]
    request_body = operation["requestBody"]
    assert request_body["required"] is True
    schema = request_body["content"]["application/json"]["schema"]
    assert set(schema["required"]) == {"source_sha256", "provenance_status", "notes"}
    assert schema["properties"]["notes"]["items"]["$ref"].startswith("#/components/schemas/")
    assert "HistoricalNote" in spec["components"]["schemas"]
    assert "$defs" not in schema


def test_chunked_upload_stops_reading_at_size_limit():
    import asyncio

    from fastapi import HTTPException, Response
    from starlette.requests import Request

    from app.api.v1.historical_notes import import_archive
    from app.persistence.models import Role, UserProfile

    consumed = 0

    async def receive():
        nonlocal consumed
        consumed += 1
        if consumed > 3:
            raise AssertionError("read beyond the first oversized chunk")
        return {"type": "http.request", "body": b"x" * 800000, "more_body": True}

    request = Request({"type": "http", "method": "PUT", "path": URL, "headers": []}, receive)
    profile = UserProfile(id=1, display_name="Synthetic", role=Role.admin)
    from app.persistence.models import Household
    household = Household(id=1, name="Synthetic")
    with pytest.raises(HTTPException) as error:
        asyncio.run(import_archive(request=request, session=None, household=household,
                                   profile=profile, response=Response(),
                                   actor="synthetic-operator"))
    assert error.value.status_code == 413
    assert consumed == 3


def test_mutations_audited_without_private_archive_fields(archive_client):
    from app.persistence.models import AuditEvent

    c = archive_client
    c.headers["X-Actor-Id"] = "synthetic-operator"
    body = manifest()
    digest = c.put(URL, json=body).json()["digest"]
    assert c.put(URL, json=body).status_code == 200
    assert c.delete(URL, params={"digest": "b" * 64}).status_code == 409
    assert c.delete(URL, params={"digest": digest}).status_code == 204
    with Session(c.app.state.engine) as s:
        events = list(s.scalars(select(AuditEvent).where(
            AuditEvent.entity_type == "historical_archive").order_by(AuditEvent.id)))
        assert [e.action for e in events] == ["import", "rollback"]
        assert all(e.actor == "synthetic-operator" for e in events)
        assert all(e.entity_id == int(c.headers["X-Profile-Id"]) for e in events)
        # No note text, source identity, provenance or digest enters the shared audit.
        assert all(e.payload is None for e in events)


@pytest.mark.parametrize("operation", ["import", "rollback"])
def test_archive_and_audit_commit_together(archive_client, monkeypatch, operation):
    from app.api.v1 import historical_notes

    c = archive_client
    digest = None
    if operation == "rollback":
        digest = c.put(URL, json=manifest()).json()["digest"]

    def fail_audit(*args, **kwargs):
        raise RuntimeError("synthetic audit write failure")

    monkeypatch.setattr(historical_notes, "emit", fail_audit)
    with pytest.raises(RuntimeError, match="synthetic audit write failure"):
        if operation == "import":
            c.put(URL, json=manifest())
        else:
            c.delete(URL, params={"digest": digest})
    remaining = c.get(URL).json()
    if operation == "import":
        assert remaining["notes"] == []
    else:
        assert remaining["digest"] == digest
        assert remaining["notes"] == manifest()["notes"]
