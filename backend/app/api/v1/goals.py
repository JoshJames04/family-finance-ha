"""Savings goals + sinking funds: CRUD, ledger, progress (#91)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, ProfileDep, SessionDep
from app.domain import goals, rbac, schedule
from app.persistence.models import Account, SavingsGoal


def _authorize(request: Request, household: HouseholdDep,
               session: SessionDep, profile: ProfileDep) -> None:
    gid = request.path_params.get("gid")
    if gid is None:
        return
    try:
        goal_id = int(gid)
    except ValueError as error:
        raise HTTPException(404, "savings goal not found") from error
    g = _get(session, household.id, goal_id)
    a = session.get(Account, g.account_id)
    if a is None or not rbac.visible(a.owner_profile_id, profile):
        raise HTTPException(404, "savings goal not found")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not rbac.write_allowed(
            a.owner_profile_id, profile):
        raise HTTPException(403, "role cannot mutate this scope")


router = APIRouter(prefix="/savings-goals", tags=["savings-goals"],
                   dependencies=[Depends(_authorize)])


def _get(session: Session, household_id: int, gid: int) -> SavingsGoal:
    g = session.get(SavingsGoal, gid)
    if g is None or g.household_id != household_id:
        raise HTTPException(404, f"savings goal {gid} not found")
    return g


@router.get("", response_model=schemas.Page[schemas.SavingsGoalOut])
def list_goals(
    household: HouseholdDep, session: SessionDep, profile: ProfileDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> schemas.Page[schemas.SavingsGoalOut]:
    stmt = select(SavingsGoal).where(
        SavingsGoal.household_id == household.id)
    if not include_inactive:
        stmt = stmt.where(SavingsGoal.active.is_(True))
    rows = [g for g in session.execute(stmt).scalars().all()
            if (account := session.get(Account, g.account_id)) is not None
            and rbac.visible(account.owner_profile_id, profile)]
    return schemas.Page(
        items=[schemas.SavingsGoalOut.model_validate(g) for g in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.post("", response_model=schemas.SavingsGoalOut, status_code=201)
def create_goal(
    body: schemas.SavingsGoalIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep, profile: ProfileDep,
) -> schemas.SavingsGoalOut:
    a = session.get(Account, body.account_id)
    if (a is None or a.household_id != household.id
            or not rbac.visible(a.owner_profile_id, profile)):
        raise HTTPException(
            404, f"account {body.account_id} not found")
    if not rbac.write_allowed(a.owner_profile_id, profile):
        raise HTTPException(403, "role cannot mutate this scope")
    g = SavingsGoal(
        household_id=household.id, name=body.name,
        goal_type=body.goal_type, target_pence=body.target.pence,
        target_date=body.target_date, start_date=body.start_date,
        account_id=body.account_id, notes=body.notes, active=True)
    session.add(g)
    session.flush()
    schedule.rematerialize_goal(session, g)
    emit(session, household.id, actor, "create", "savings_goal", g.id,
         {"name": g.name, "goal_type": g.goal_type.value})
    return schemas.SavingsGoalOut.model_validate(g)


@router.get("/{gid}/progress",
            response_model=schemas.GoalProgressOut)
def goal_progress(
    gid: int, household: HouseholdDep, session: SessionDep,
) -> schemas.GoalProgressOut:
    g = _get(session, household.id, gid)
    p = goals.progress(session, g)
    return schemas.GoalProgressOut(
        contributed_pence=p.contributed_pence,
        remaining_pence=p.remaining_pence,
        monthly_need_pence=p.monthly_need_pence, pct=p.pct,
        average_rate_pence=p.average_rate_pence,
        projected_date=p.projected_date, on_track=p.on_track)


@router.get("/{gid}/contributions",
            response_model=list[schemas.GoalContributionOut])
def goal_ledger(
    gid: int, household: HouseholdDep, session: SessionDep,
) -> list[schemas.GoalContributionOut]:
    """The contribution ledger — real inbound transfer legs (#91)."""
    g = _get(session, household.id, gid)
    return [schemas.GoalContributionOut(
        transaction_id=t.id, date=t.date,
        amount_pence=t.amount_pence, payee=t.payee)
        for t in goals.ledger(session, g)]


@router.delete("/{gid}", status_code=204)
def deactivate_goal(
    gid: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> None:
    g = _get(session, household.id, gid)
    g.active = False
    schedule.rematerialize_goal(session, g)
    emit(session, household.id, actor, "deactivate", "savings_goal",
         g.id)
