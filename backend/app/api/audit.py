"""Audit emission — every mutation route emits an event.

Payload hygiene (issue #31): values under amount-like keys
(*pence*, *amount*, *balance*) are stored as sha256 digests, never as
raw figures — the audit log stays useful for comparison without leaking
financial amounts to anyone reading the table directly.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.persistence.models import AuditEvent

_SENSITIVE_KEY = re.compile(r"pence|amount|balance", re.IGNORECASE)


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _scrub(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        k: _digest(v) if _SENSITIVE_KEY.search(k) else v
        for k, v in payload.items()
    }


def emit(
    session: Session,
    household_id: int,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: int,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    e = AuditEvent(
        household_id=household_id, actor=actor, action=action,
        entity_type=entity_type, entity_id=entity_id,
        payload=json.dumps(_scrub(payload)) if payload else None,
    )
    session.add(e)
    return e
