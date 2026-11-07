"""Structured logging redaction + problem-detail errors."""

import json
import logging

from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter, _redact
from app.main import app


def test_redact_dict_secret_keys() -> None:
    out = _redact({"access_token": "abc123", "note": "token=abc123"})
    assert out["access_token"] == "***"
    assert "***" in out["note"] and "abc123" not in out["note"]


def test_redact_bearer() -> None:
    assert "xyz" not in _redact("Authorization: Bearer xyz")


def test_json_formatter_redacts(caplog) -> None:
    rec = logging.getLogger("t").makeRecord(
        "t", logging.INFO, "f", 1, "token=sekrit", (), None
    )
    line = JsonFormatter().format(rec)
    assert "sekrit" not in json.loads(line)["msg"]


def test_validation_error_is_problem_detail() -> None:
    r = TestClient(app).get("/api/v1/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["status"] == 404 and "title" in body
    assert r.headers["content-type"].startswith("application/problem+json")


def test_request_id_echoed() -> None:
    r = TestClient(app).get("/healthz", headers={"x-request-id": "req-123"})
    assert r.headers["x-request-id"] == "req-123"
