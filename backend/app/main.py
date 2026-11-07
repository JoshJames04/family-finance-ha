"""Family Finance backend — application entry point."""

from __future__ import annotations

import logging

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.errors import install_error_handlers
from app.core.health import database_check, registry
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.settings import Settings

log = logging.getLogger(__name__)

api = APIRouter(prefix="/api/v1")


@api.get("/version")
def version() -> dict[str, str]:
    return {"version": __version__}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    configure_logging(settings.log_level)

    app = FastAPI(title="Family Finance", version=__version__)
    app.state.settings = settings
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(api)

    registry.register(
        "database", lambda: database_check(settings.resolved_database_url)
    )

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
