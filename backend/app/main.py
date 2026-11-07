"""Family Finance backend — application entry point.

The full platform skeleton (settings, structured logging, health/readiness,
versioned API) is delivered under issue #20; this module provides the
bootable shell required by the dev environment.
"""

from __future__ import annotations

import uvicorn
from fastapi import APIRouter, FastAPI

from app import __version__

api = APIRouter(prefix="/api/v1")


@api.get("/version")
def version() -> dict[str, str]:
    return {"version": __version__}


def create_app() -> FastAPI:
    app = FastAPI(title="Family Finance", version=__version__)
    app.include_router(api)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def run() -> None:
    uvicorn.run("app.main:app", host="0.0.0.0", port=8099)


if __name__ == "__main__":
    run()
