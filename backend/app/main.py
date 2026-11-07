"""Family Finance backend — application entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.errors import install_error_handlers
from app.core.health import ComponentStatus, database_check, registry
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.settings import Settings
from app.persistence.engine import create_app_engine, integrity_check
from app.persistence.migrate import migrations_current, upgrade_head
from app.persistence.session import init_session_factory

log = logging.getLogger(__name__)

api = APIRouter(prefix="/api/v1")


@api.get("/version")
def version() -> dict[str, str]:
    return {"version": __version__}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_app_engine(settings.resolved_database_url)
        upgrade_head(settings.resolved_database_url)
        init_session_factory(engine)
        app.state.engine = engine
        if not integrity_check(engine):
            log.error("database integrity_check failed on boot")
        yield
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
