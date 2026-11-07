"""Family Finance backend — application entry point."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.errors import install_error_handlers
from app.api.v1 import v1_router
from app.core.health import ComponentStatus, database_check, registry
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.settings import Settings
from app.persistence import backup
from app.persistence.engine import create_app_engine, integrity_check
from app.persistence.migrate import migrations_current, upgrade_head
from app.persistence.session import init_session_factory

log = logging.getLogger(__name__)

api = APIRouter(prefix="/api/v1")
api.include_router(v1_router)


@api.get("/version")
def version() -> dict[str, str]:
    return {"version": __version__}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db_url = settings.resolved_database_url
        db_path = settings.database_path
        backup_dir = settings.resolved_backup_dir

        # Pending restore request from a previous session applies first,
        # before the engine opens the file (issue #24).
        if db_path is not None:
            pending = backup.take_pending_restore(
                settings.restore_marker)
            if pending is not None:
                snap = backup_dir / pending
                if snap.is_file():
                    log.warning("applying pending restore: %s", pending)
                    backup.restore_from(snap, db_path)
                else:
                    log.error("pending restore %s missing — ignored",
                              pending)

        engine = create_app_engine(db_url)
        existed = db_path is not None and db_path.exists()

        # Pre-migration snapshot; a failed upgrade restores it and the
        # app boots degraded rather than corrupting data (issue #24).
        snapshot = None
        if existed and db_path is not None:
            snapshot = backup.create_snapshot(engine, backup_dir)
            backup.rotate(backup_dir, settings.backup_keep)
        # Connectivity probe outside the try: an unreachable DB still
        # refuses to start; only a *failed migration on an open DB* is
        # recoverable via the snapshot (issue #24).
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        try:
            upgrade_head(db_url)
        except Exception as exc:
            log.exception("migration failed — restoring snapshot")
            app.state.boot_error = f"migration failed: {exc}"
            if snapshot is not None and db_path is not None:
                engine.dispose()
                backup.restore_from(snapshot, db_path)
                engine = create_app_engine(db_url)

        init_session_factory(engine)
        app.state.engine = engine
        if not integrity_check(engine):
            log.error("database integrity_check failed on boot")

        async def _snapshot_loop() -> None:
            while True:
                await asyncio.sleep(
                    settings.backup_interval_hours * 3600)
                try:
                    backup.create_snapshot(engine, backup_dir)
                    n = backup.rotate(backup_dir, settings.backup_keep)
                    log.info("scheduled snapshot taken (%d rotated)", n)
                except Exception:
                    log.exception("scheduled backup failed")

        task = (asyncio.create_task(_snapshot_loop())
                if settings.backup_interval_hours > 0 else None)
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            engine.dispose()

    app = FastAPI(title="Family Finance", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(api)

    registry.register(
        "database", lambda: database_check(settings.resolved_database_url)
    )

    def _migrations() -> ComponentStatus:
        engine = getattr(app.state, "engine", None)
        if engine is None:
            return ComponentStatus("migrations", False, "engine not ready")
        ok = migrations_current(engine, settings.resolved_database_url)
        return ComponentStatus("migrations", ok, "current" if ok else "behind")

    registry.register("migrations", _migrations)

    def _boot() -> ComponentStatus:
        err = getattr(app.state, "boot_error", None)
        return ComponentStatus("boot", err is None, err or "clean")

    registry.register("boot", _boot)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request) -> JSONResponse:
        results = registry.run()
        ready = all(r.ok for r in results)
        body = {
            "status": "ready" if ready else "degraded",
            "env": settings.env,
            "components": {r.name: {"ok": r.ok, "detail": r.detail}
                           for r in results},
        }
        log.info("readiness: %s", body["status"])
        return JSONResponse(body, status_code=200 if ready else 503)

    return app


app = create_app()


def run() -> None:
    uvicorn.run("app.main:app", host="0.0.0.0", port=8099)


if __name__ == "__main__":
    run()
