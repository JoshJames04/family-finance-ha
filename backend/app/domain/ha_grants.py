"""Household-local HA read grants; never a member identity or write permission."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.persistence import models


def key(household_id: int) -> str:
    return f"ha_private_grants:{household_id}"


def stored(session: Session, household_id: int) -> dict[str, Any]:
    row = session.get(models.AppMeta, key(household_id))
    try:
        value = json.loads(row.value) if row else {}
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def active_profile(session: Session, household_id: int, profile_id: int) -> bool:
    p = session.get(models.UserProfile, profile_id)
    return bool(p and p.household_id == household_id and p.is_active)


def profile_allowed(session: Session, household_id: int, profile_id: int) -> bool:
    return profile_id in stored(session, household_id).get("profile_ids", []) and active_profile(
        session, household_id, profile_id
    )


def account_allowed(session: Session, household_id: int, account: models.Account) -> bool:
    if account.household_id != household_id or account.archived:
        return False
    if account.owner_profile_id is None:
        return True
    grants = stored(session, household_id).get("accounts", {})
    return (
        isinstance(grants, dict)
        and grants.get(str(account.id)) == account.owner_profile_id
        and active_profile(session, household_id, account.owner_profile_id)
    )


def scope(session: Session, event: models.IntegrationEvent) -> dict[str, Any] | None:
    """Resolve only explicitly supported grant resources, including payroll destinations."""
    entity: Any
    if event.entity_kind == "employment":
        entity = session.get(models.Employment, event.entity_id)
    elif event.entity_kind == "payroll_run":
        run = session.get(models.PayrollRun, event.entity_id)
        entity = session.get(models.Employment, run.employment_id) if run else None
    elif event.entity_kind == "payslip":
        slip = session.get(models.Payslip, event.entity_id)
        run = session.get(models.PayrollRun, slip.payroll_run_id) if slip else None
        entity = session.get(models.Employment, run.employment_id) if run else None
    else:
        return None
    if entity is None or getattr(entity, "household_id", event.household_id) != event.household_id:
        return None
    owner = entity.profile_id
    account_id = entity.paid_into_account_id
    account = session.get(models.Account, account_id) if account_id else None
    if account_id is not None and (account is None or account.household_id != event.household_id):
        return None
    return {
        "owner": owner,
        "account": account_id,
        "account_owner": account.owner_profile_id if account else None,
    }


def event_allowed(session: Session, event: models.IntegrationEvent) -> bool:
    current = scope(session, event)
    # No widening of legacy rows with unrecorded original private ownership.
    if current is None or event.payload.get("_ha_scope") != current:
        return False
    owner = current["owner"]
    if owner is not None and not profile_allowed(session, event.household_id, owner):
        return False
    account_id = current["account"]
    if account_id is not None:
        account = session.get(models.Account, account_id)
        if account is None or not account_allowed(session, event.household_id, account):
            return False
    return owner is not None or current["account_owner"] is not None
