"""In-process runtime telemetry (issue #460).

A single counters object on ``app.state.telemetry`` — written by the
scheduler loop, request middleware and the unhandled-error handler,
read (sanitised) by ``/diagnostics``. Counts and timings only — no
financial or personal data is ever recorded here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class RuntimeTelemetry:
    ticks_total: int = 0
    tick_errors: int = 0
    last_tick_at: datetime | None = None
    last_tick_ms: float | None = None
    last_tick_error: str | None = None
    db_locked_total: int = 0
    requests_total: int = 0
    errors_5xx: int = 0

    def record_tick(self, ms: float, error: str | None) -> None:
        self.ticks_total += 1
        self.last_tick_at = datetime.now()
        self.last_tick_ms = round(ms, 1)
        self.last_tick_error = error
        if error is not None:
            self.tick_errors += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "ticks_total": self.ticks_total,
            "tick_errors": self.tick_errors,
            "last_tick_at": self.last_tick_at,
            "last_tick_ms": self.last_tick_ms,
            "last_tick_error": self.last_tick_error,
            "db_locked_total": self.db_locked_total,
            "requests_total": self.requests_total,
            "errors_5xx": self.errors_5xx,
        }
