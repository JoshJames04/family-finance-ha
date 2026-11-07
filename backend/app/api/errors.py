"""RFC 9457-style problem-detail error responses."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


def problem(status: int, title: str, detail: str | None = None,
            type_: str = "about:blank", **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {"type": type_, "title": title, "status": status}
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status,
                        media_type="application/problem+json")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exc(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(exc.status_code,
                       str(exc.detail) if exc.detail else "HTTP error",
                       type_="about:blank")

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_: Request,
                             exc: RequestValidationError) -> JSONResponse:
        return problem(422, "Validation failed",
                       detail="Request failed schema validation",
                       errors=exc.errors())

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return problem(500, "Internal server error")
