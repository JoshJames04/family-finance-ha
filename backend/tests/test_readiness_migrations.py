"""Readiness reports the migrations component after startup."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


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
