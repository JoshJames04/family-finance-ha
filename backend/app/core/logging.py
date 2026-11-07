"""Structured JSON logging with secret redaction and request IDs."""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

SECRET_PATTERNS = [
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"(token|password|secret|api_key|key)=[\"']?\S+", re.IGNORECASE),
]
SECRET_KEYS = {"token", "password", "secret", "api_key", "authorization",
               "supervisor_token", "access_token", "refresh_token"}


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        for pat in SECRET_PATTERNS:
            value = pat.sub(lambda m: m.group(0).split("=")[0] + "=***"
                            if "=" in m.group(0) else "***", value)
        return value
    if isinstance(value, dict):
        return {k: ("***" if k.lower() in SECRET_KEYS else _redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_ctx.get()),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, val in getattr(record, "extra", {}).items():
            payload[key] = _redact(val)
        payload["msg"] = _redact(payload["msg"])
        return json.dumps(payload, default=str)


class SecretRedactionFilter(logging.Filter):
    """Belt-and-braces: redact the rendered message too."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg)
        if record.args:
            record.args = tuple(_redact(a) for a in record.args)
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretRedactionFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel("WARNING")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = rid
            return response
        finally:
            request_id_ctx.reset(token)
