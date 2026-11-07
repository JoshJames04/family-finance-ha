"""What-if scenarios (issue #76): saveable op layers that replay the
projection against an adjusted movement stream. Sandbox ops never
touch real ExpectedMovement rows."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.api.v1.accounts import projection_out
from app.domain import scenario as scen
from app.domain.scenario import ScenarioError
from app.persistence.models import Scenario, ScenarioOpKind

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _get(
    session: Session, household_id: int, scenario_id: int
) -> Scenario:
    s = scen.get_scenario(session, household_id, scenario_id)
    if s is None:
        raise HTTPException(404, f"scenario {scenario_id} not found")
    return s


@router.get("", response_model=list[schemas.ScenarioOut])
def list_scenarios(
    household: HouseholdDep, session: SessionDep,
) -> list[schemas.ScenarioOut]:
    return [schemas.ScenarioOut.model_validate(s)
            for s in scen.list_scenarios(session, household.id)]


@router.post("", response_model=schemas.ScenarioOut, status_code=201)
def create_scenario(
    body: schemas.ScenarioIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.ScenarioOut:
    s = scen.create_scenario(session, household.id, body.name)
    emit(session, household.id, actor, "create", "scenario", s.id,
         {"name": body.name})
    return schemas.ScenarioOut.model_validate(s)


@router.delete("/{scenario_id}", status_code=204)
def delete_scenario(
    scenario_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> None:
    s = _get(session, household.id, scenario_id)
    emit(session, household.id, actor, "delete", "scenario", s.id)
    scen.delete_scenario(session, s)


@router.post("/{scenario_id}/ops",
             response_model=schemas.ScenarioOpOut, status_code=201)
def add_op(
    scenario_id: int, body: schemas.ScenarioOpIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.ScenarioOpOut:
    s = _get(session, household.id, scenario_id)
    try:
        op = scen.add_op(
            session, s, kind=ScenarioOpKind(body.kind),
            expected_movement_id=body.expected_movement_id,
            account_id=body.account_id,
            expected_date=body.expected_date,
            amount_pence=(body.amount.pence
                          if body.amount is not None else None),
            label=body.label)
    except ScenarioError as e:
        raise HTTPException(422, str(e)) from e
    emit(session, household.id, actor, "add_op", "scenario", s.id,
         {"op_id": op.id, "kind": body.kind})
    return schemas.ScenarioOpOut.model_validate(op)


@router.get("/{scenario_id}/run",
            response_model=schemas.ScenarioRunOut)
def run_scenario(
    scenario_id: int, household: HouseholdDep, session: SessionDep,
    account_id: Annotated[int, Query()],
    days: Annotated[int, Query(ge=1, le=731)] = 90,
) -> schemas.ScenarioRunOut:
    """Baseline vs scenario projection + structured diff."""
    from app.api.v1.accounts import _get as get_account
    s = _get(session, household.id, scenario_id)
    a = get_account(session, household.id, account_id)
    r = scen.run(session, s, a, days)
    d = r.diff
    return schemas.ScenarioRunOut(
        scenario_id=r.scenario_id, account_id=r.account_id,
        days=r.days,
        baseline=projection_out(r.baseline),
        projection=projection_out(r.projection),
        diff=schemas.ScenarioDiffOut(
            min_balance_changed=d.min_balance_changed,
            baseline_min_balance_pence=d.baseline_min_balance_pence,
            baseline_min_balance_date=d.baseline_min_balance_date,
            scenario_min_balance_pence=d.scenario_min_balance_pence,
            scenario_min_balance_date=d.scenario_min_balance_date,
            min_balance_delta_pence=d.min_balance_delta_pence,
            safe_to_spend_delta_pence=d.safe_to_spend_delta_pence,
            paydays=[schemas.PaydayDeltaOut(
                date=p.date,
                baseline_balance_pence=p.baseline_balance_pence,
                scenario_balance_pence=p.scenario_balance_pence,
                delta_pence=p.delta_pence) for p in d.paydays],
        ))
