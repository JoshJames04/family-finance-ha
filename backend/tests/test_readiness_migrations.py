"""Readiness reports the migrations component after startup."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app
from app.persistence.migrate import upgrade_head


def test_failed_upgrade_blocks_financial_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"sqlite:///{(tmp_path / 'failed.db').as_posix()}"
    upgrade_head(url)
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_DATABASE_URL": url,
                              "FF_SCHEDULER_TICK_SECONDS": "0.01",
                              "FF_INGRESS": "true", "FF_ADMIN_TOKEN": "synthetic-recovery",
                              "FF_BACKUP_INTERVAL_HOURS": "0"})

    def fail(_url: str) -> None:
        raise RuntimeError("private migration detail")

    monkeypatch.setattr("app.main.upgrade_head", fail)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503
        assert "private migration detail" not in client.get("/readyz").text
        response = client.post("/api/v1/households", json={"name": "Blocked"})
        assert response.status_code == 503
        assert app.state.scheduler_enabled is False
        assert client.get("/api/v1/version").status_code == 200
        assert client.get("/api/v1/admin/backups", headers={
            "X-Admin-Token": "synthetic-recovery"}).status_code == 200


def test_readyz_shows_migrations_current(tmp_path: Path) -> None:
    settings = Settings.load({
        "FF_DATA_DIR": str(tmp_path),
        "FF_DATABASE_URL": f"sqlite:///{(tmp_path / 'm.db').as_posix()}",
    })
    with TestClient(create_app(settings)) as client:
        r = client.get("/readyz")
        assert r.status_code == 200
        comps = r.json()["components"]
        assert comps["migrations"] == {"ok": True, "detail": "current"}
        assert comps["database"]["ok"] is True


def test_startup_creates_schema(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path),
                              "FF_DATABASE_URL": f"sqlite:///{db.as_posix()}"})
    with TestClient(create_app(settings)):
        pass
    assert db.exists()
