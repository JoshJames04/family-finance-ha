"""Smoke tests for the bootable app shell."""

from fastapi.testclient import TestClient

from app.main import app


def test_healthz() -> None:
    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}


def test_version_endpoint() -> None:
    client = TestClient(app)
    body = client.get("/api/v1/version").json()
    assert "version" in body


def test_openapi_generated() -> None:
    client = TestClient(app)
    assert client.get("/openapi.json").status_code == 200
