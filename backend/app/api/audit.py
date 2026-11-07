"""Audit emission — every mutation route emits an event."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.persistence.models import AuditEvent


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
        payload=json.dumps(payload) if payload else None,
    )
    session.add(e)
    return e
