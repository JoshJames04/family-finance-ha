"""Budgets: CRUD, lines, lifecycle transitions, per-line actuals."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import budgets as dom
from app.persistence.models import (
    Budget,
    BudgetLine,
    BudgetStatus,
    Category,
)

router = APIRouter(prefix="/budgets", tags=["budgets"])


def _get(session: Session, household_id: int, budget_id: int) -> Budget:
    b = session.execute(
        select(Budget).where(Budget.id == budget_id)
        .options(selectinload(Budget.lines))
    ).scalar_one_or_none()
    if b is None or b.household_id != household_id:
        raise HTTPException(404, f"budget {budget_id} not found")
    return b


def _detail(session: Session, b: Budget) -> schemas.BudgetDetail:
    return schemas.BudgetDetail(
        **schemas.BudgetOut.model_validate(b).model_dump(),
        lines=[schemas.BudgetLineOut.model_validate(line)
               for line in b.lines],
        read_only=dom.is_read_only(session, b))


@router.get("", response_model=schemas.Page[schemas.BudgetOut])
def list_budgets(
    household: HouseholdDep, session: SessionDep,
    status: Annotated[BudgetStatus | None, Query()] = None,
) -> schemas.Page[schemas.BudgetOut]:
    q = select(Budget).where(Budget.household_id == household.id)
    if status is not None:
        q = q.where(Budget.status == status)
    rows = session.execute(q.order_by(Budget.start_date.desc())
                           ).scalars().all()
    return schemas.Page(
        items=[schemas.BudgetOut.model_validate(b) for b in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.post("", response_model=schemas.BudgetOut, status_code=201)
def create_budget(
    body: schemas.BudgetIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.BudgetOut:
    b = dom.create_budget(
        session, household.id, body.name, body.period_type,
        body.start_date, body.end_date,
        owner_profile_id=body.owner_profile_id)
    emit(session, household.id, actor, "create", "budget", b.id,
         {"name": b.name, "period_type": b.period_type.value})
    return schemas.BudgetOut.model_validate(b)


@router.get("/{budget_id}", response_model=schemas.BudgetDetail)
def get_budget(
    budget_id: int, household: HouseholdDep, session: SessionDep,
) -> schemas.BudgetDetail:
    return _detail(session, _get(session, household.id, budget_id))


@router.post("/{budget_id}/lines",
             response_model=schemas.BudgetLineOut, status_code=201)
def add_line(
    budget_id: int, body: schemas.BudgetLineIn,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.BudgetLineOut:
    b = _get(session, household.id, budget_id)
    cat = session.get(Category, body.category_id)
    if cat is None or cat.household_id != household.id:
        raise HTTPException(404, "category not found")
    line = dom.add_line(session, b, body.category_id, body.side,
                        body.amount.pence)
    emit(session, household.id, actor, "create", "budget_line", line.id,
         {"budget_id": b.id, "category_id": line.category_id})
    return schemas.BudgetLineOut.model_validate(line)


@router.patch("/{budget_id}/lines/{line_id}",
              response_model=schemas.BudgetLineOut)
def revise_line(
    budget_id: int, line_id: int, body: schemas.BudgetLineRevise,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.BudgetLineOut:
    b = _get(session, household.id, budget_id)
    line = session.get(BudgetLine, line_id)
    if line is None or line.budget_id != b.id:
        raise HTTPException(404, "line not found")
    line = dom.revise_line(session, line, body.amount.pence,
                           body.reason)
    emit(session, household.id, actor, "update", "budget_line", line.id,
         {"revised_pence": line.revised_pence,
          "reason": body.reason})
    return schemas.BudgetLineOut.model_validate(line)


@router.post("/{budget_id}/approve", response_model=schemas.BudgetOut)
def approve_budget(
    budget_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> schemas.BudgetOut:
    b = dom.approve(session, _get(session, household.id, budget_id))
    emit(session, household.id, actor, "update", "budget", b.id,
         {"status": b.status.value})
    return schemas.BudgetOut.model_validate(b)


@router.post("/{budget_id}/lock", response_model=schemas.BudgetOut)
def lock_budget(
    budget_id: int, household: HouseholdDep, session: SessionDep,
    actor: ActorDep,
) -> schemas.BudgetOut:
    b = dom.lock(session, _get(session, household.id, budget_id))
    emit(session, household.id, actor, "update", "budget", b.id,
         {"status": b.status.value})
    return schemas.BudgetOut.model_validate(b)


@router.get("/{budget_id}/actuals",
            response_model=schemas.BudgetActuals)
def budget_actuals(
    budget_id: int, household: HouseholdDep, session: SessionDep,
) -> schemas.BudgetActuals:
    b = _get(session, household.id, budget_id)
    totals = dom.actuals_for_budget(session, b)
    lines = {line.id: dom.line_analysis(totals[line.id], line)
             for line in b.lines}
    return schemas.BudgetActuals(
        budget_id=b.id, totals=totals, lines=lines)
