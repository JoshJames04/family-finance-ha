"""Configurable transfer rules: CRUD + materialise + evaluate (#86)."""
from __future__ import annotations

import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import schedule, transfer_rules
from app.persistence.models import (
    Account,
    AmountKind,
    Confidence,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    TransferRule,
)

router = APIRouter(prefix="/transfer-rules", tags=["transfer-rules"])


def _get(session: Session, household_id: int, rid: int) -> TransferRule:
    r = session.get(TransferRule, rid)
    if r is None or r.household_id != household_id:
        raise HTTPException(404, f"transfer rule {rid} not found")
    return r


def _check_accounts(
    session: Session, household_id: int,
    source_id: int, dest_id: int,
) -> None:
    for aid in (source_id, dest_id):
        a = session.get(Account, aid)
        if a is None or a.household_id != household_id:
            raise HTTPException(404, f"account {aid} not found")
    if source_id == dest_id:
        raise HTTPException(
            422, "source and destination accounts must differ")


@router.get("", response_model=schemas.Page[schemas.TransferRuleOut])
def list_rules(
    household: HouseholdDep, session: SessionDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> schemas.Page[schemas.TransferRuleOut]:
    stmt = select(TransferRule).where(
        TransferRule.household_id == household.id)
    if not include_inactive:
        stmt = stmt.where(TransferRule.active.is_(True))
    rows = list(session.execute(stmt).scalars().all())
    return schemas.Page(
        items=[schemas.TransferRuleOut.model_validate(r) for r in rows],
        total=len(rows), page=1, size=len(rows) or 1)


def _ensure_space_leg(
    session: Session, household_id: int, actor: str,
    parent: Account, space: Account, rule: TransferRule,
) -> None:
    """Paired internal main→space rule for a space destination
    (issue #345): the bank only moves main→main externally, so the
    external leg targets the space's parent account and this derived
    leg lands the money in the space. Skipped when an active rule
    already covers the same parent→space flow."""
    exists = session.scalar(select(TransferRule).where(
        TransferRule.household_id == household_id,
        TransferRule.source_account_id == parent.id,
        TransferRule.dest_account_id == space.id,
        TransferRule.active.is_(True)))
    if exists is not None:
        return
    leg = TransferRule(
        household_id=household_id, name=f"{rule.name} → {space.name}",
        source_account_id=parent.id, dest_account_id=space.id,
        amount_spec=rule.amount_spec, frequency=rule.frequency,
        day_of_month=rule.day_of_month, start_date=rule.start_date,
        end_date=rule.end_date, notes=rule.notes, active=True)
    session.add(leg)
    session.flush()
    schedule.rematerialize_transfer_rule(session, leg)
    emit(session, household_id, actor, "create", "transfer_rule",
         leg.id, {"name": leg.name, "derived": "space_leg",
                  "for_rule": rule.id})


@router.post("", response_model=schemas.TransferRuleOut, status_code=201)
def create_rule(
    body: schemas.TransferRuleIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.TransferRuleOut:
    _check_accounts(session, household.id, body.source_account_id,
                    body.dest_account_id)
    spec = transfer_rules.parse_spec(body.amount_spec.model_dump())
    parent = transfer_rules.space_parent(session, body.dest_account_id)
    external_dest = (parent.id
                     if parent is not None
                     and parent.id != body.source_account_id
                     else body.dest_account_id)
    r = TransferRule(
        household_id=household.id, name=body.name,
        source_account_id=body.source_account_id,
        dest_account_id=external_dest,
        amount_spec=json.dumps(body.amount_spec.model_dump()),
        frequency=body.frequency, day_of_month=body.day_of_month,
        start_date=body.start_date, end_date=body.end_date,
        notes=body.notes, active=True)
    session.add(r)
    session.flush()
    schedule.rematerialize_transfer_rule(session, r)
    emit(session, household.id, actor, "create", "transfer_rule", r.id,
         {"name": r.name, "kind": spec.kind.value})
    if parent is not None and external_dest != body.dest_account_id:
        space = session.get(Account, body.dest_account_id)
        if space is not None:
            _ensure_space_leg(session, household.id, actor, parent,
                              space, r)
    return schemas.TransferRuleOut.model_validate(r)


@router.patch("/{rid}", response_model=schemas.TransferRuleOut)
def update_rule(
    rid: int, body: schemas.TransferRulePatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.TransferRuleOut:
    r = _get(session, household.id, rid)
    changes = body.model_dump(exclude_unset=True)
    # Explicit null is only meaningful for nullable columns — drop it
    # elsewhere rather than breaking the NOT NULL constraint on flush.
    for k in ("name", "source_account_id", "dest_account_id",
              "frequency", "day_of_month", "start_date", "active"):
        if changes.get(k) is None:
            changes.pop(k, None)
    if {"source_account_id", "dest_account_id"} & changes.keys():
        _check_accounts(
            session, household.id,
            changes.get("source_account_id", r.source_account_id),
            changes.get("dest_account_id", r.dest_account_id))
    if "amount_spec" in changes and changes["amount_spec"] is not None:
        changes["amount_spec"] = json.dumps(changes["amount_spec"])
    for k, v in changes.items():
        setattr(r, k, v)
    session.flush()
    # A space destination auto-derives the paired internal leg — the
    # external leg must land on the space's parent main account (#345).
    space = session.get(Account, r.dest_account_id)
    parent = transfer_rules.space_parent(session, r.dest_account_id)
    if (parent is not None and space is not None
            and parent.id != r.source_account_id):
        r.dest_account_id = parent.id
        session.flush()
        _ensure_space_leg(session, household.id, actor, parent,
                          space, r)
    # spec/timing edits regenerate future rows only — history untouched
    schedule.rematerialize_transfer_rule(session, r)
    emit(session, household.id, actor, "update", "transfer_rule", r.id,
         {"fields": sorted(changes)})
    return schemas.TransferRuleOut.model_validate(r)


@router.delete("/{rid}", status_code=204)
def delete_rule(
    rid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> None:
    """Permanent removal (issue #344): every *unmatched* expected
    movement the rule generated goes with it — matched rows stay:
    ``expected_match`` FKs to them and real money already met the
    obligation. Pause is the softer option (PATCH ``active=false``)."""
    r = _get(session, household.id, rid)
    session.execute(
        delete(ExpectedMovement).where(
            ExpectedMovement.source_type == ExpectedSource.transfer_rule,
            ExpectedMovement.source_id == r.id,
            ExpectedMovement.id.not_in(
                select(ExpectedMatch.movement_id))))
    session.delete(r)
    session.flush()
    emit(session, household.id, actor, "delete", "transfer_rule", rid,
         {"name": r.name})


@router.post("/{rid}/materialise", status_code=200)
def materialise_rule(
    rid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> dict[str, int]:
    r = _get(session, household.id, rid)
    n = schedule.rematerialize_transfer_rule(session, r)
    emit(session, household.id, actor, "materialise", "transfer_rule",
         r.id, {"generated": n})
    return {"generated": n}


@router.get("/{rid}/evaluate", response_model=schemas.EvaluateResult)
def evaluate_rule(
    rid: int, household: HouseholdDep, session: SessionDep,
    on: Annotated[date, Query()],
) -> schemas.EvaluateResult:
    r = _get(session, household.id, rid)
    amount = transfer_rules.evaluate(session, r, on)
    kind = transfer_rules.parse_spec(r.amount_spec).kind
    return schemas.EvaluateResult(
        amount_pence=amount,
        confidence=(Confidence.scheduled
                    if kind is AmountKind.fixed else Confidence.estimated))
