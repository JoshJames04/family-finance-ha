"""Health/readiness transitions incl. degraded DB."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


def make_client(tmp_path: Path, **env: str) -> TestClient:
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path), **env})
    return TestClient(create_app(settings))


def test_healthz_always_ok(tmp_path: Path) -> None:
    assert make_client(tmp_path).get("/healthz").status_code == 200


def test_readyz_ok_with_db(tmp_path: Path) -> None:
    r = make_client(tmp_path).get("/readyz")
    assert r.status_code == 200
    assert r.json()["components"]["database"]["ok"] is True


def test_readyz_degraded_when_db_unreachable(tmp_path: Path) -> None:
    # Point the DB at a path that cannot be opened (directory, not a file)
    settings = Settings.load({
        "FF_DATA_DIR": str(tmp_path),
        "FF_DATABASE_URL": f"sqlite:///{tmp_path.as_posix()}",
    })
    r = TestClient(create_app(settings)).get("/readyz")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert r.json()["components"]["database"]["ok"] is False
