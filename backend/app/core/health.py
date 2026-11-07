"""Health & readiness: liveness plus a component-check registry.

Components register a check at startup (e.g. the persistence layer registers
a DB ping + migration-version check). ``/readyz`` aggregates them: 200 only
when every component is healthy, otherwise 503 with per-component detail.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ComponentStatus:
    name: str
    ok: bool
    detail: str = ""


CheckFn = Callable[[], ComponentStatus]


@dataclass
class HealthRegistry:
    _checks: dict[str, CheckFn] = field(default_factory=dict)

    def register(self, name: str, check: CheckFn) -> None:
        self._checks[name] = check

    def run(self) -> list[ComponentStatus]:
        results: list[ComponentStatus] = []
        for name, check in self._checks.items():
            try:
                results.append(check())
            except Exception as exc:  # a failed check must not 500 readiness
                log.exception("health check %s raised", name)
                results.append(ComponentStatus(name, False, f"{type(exc).__name__}"))
        return results


registry = HealthRegistry()


def database_check(engine_url: str) -> ComponentStatus:
    """Ping the DB. Kept dependency-light; #21 swaps in the real session."""
    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(engine_url, connect_args={"timeout": 2}
                               if engine_url.startswith("sqlite") else {})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return ComponentStatus("database", True)
    except Exception as exc:
        return ComponentStatus("database", False, type(exc).__name__)
