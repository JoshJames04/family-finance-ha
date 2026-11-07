"""Health/readiness transitions incl. degraded DB."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


def test_healthz_always_ok(tmp_path: Path) -> None:
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200


def test_readyz_ok_with_db(tmp_path: Path) -> None:
    settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
    with TestClient(create_app(settings)) as client:
        r = client.get("/readyz")
        assert r.status_code == 200
        assert r.json()["components"]["database"]["ok"] is True


def test_readyz_degraded_when_component_fails_post_start(tmp_path: Path) -> None:
    """A component failing after startup degrades readiness (503)."""
    from app.core.health import ComponentStatus

    settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.registry.register(
            "database",
            lambda: ComponentStatus("database", False, "connection lost"),
        )
        r = client.get("/readyz")
        assert r.status_code == 503
        assert r.json()["status"] == "degraded"
        assert r.json()["components"]["database"]["ok"] is False


def test_app_refuses_to_start_on_unreachable_db(tmp_path: Path) -> None:
    """Failed migration/connectivity at boot = fail fast, not silent corruption."""
    import pytest
    import sqlalchemy.exc

    settings = Settings.load({
        "FF_DATA_DIR": str(tmp_path),
        "FF_DATABASE_URL": f"sqlite:///{tmp_path.as_posix()}",
    })
    with pytest.raises(sqlalchemy.exc.OperationalError), \
            TestClient(create_app(settings)):
        pass
