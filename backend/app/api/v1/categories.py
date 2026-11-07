"""Categories: hierarchy CRUD with cycle prevention."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.api.audit import emit
from app.api.deps import ActorDep, HouseholdDep, SessionDep
from app.domain import categories as cats
from app.persistence.models import Category

router = APIRouter(prefix="/categories", tags=["categories"])


def _get(
    session: Session, household_id: int, category_id: int
) -> Category:
    c = session.get(Category, category_id)
    if c is None or c.household_id != household_id:
        raise HTTPException(404, f"category {category_id} not found")
    return c


@router.get("", response_model=schemas.Page[schemas.CategoryOut])
def list_categories(
    household: HouseholdDep, session: SessionDep,
    include_archived: Annotated[bool, Query()] = False,
) -> schemas.Page[schemas.CategoryOut]:
    stmt = select(Category).where(
        Category.household_id == household.id)
    if not include_archived:
        stmt = stmt.where(Category.archived.is_(False))
    rows = list(session.execute(
        stmt.order_by(Category.parent_id.nulls_first(), Category.name)
    ).scalars().all())
    return schemas.Page(
        items=[schemas.CategoryOut.model_validate(c) for c in rows],
        total=len(rows), page=1, size=len(rows) or 1)


@router.post("", response_model=schemas.CategoryOut, status_code=201)
def create_category(
    body: schemas.CategoryIn, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> schemas.CategoryOut:
    if body.parent_id is not None:
        _get(session, household.id, body.parent_id)
    c = cats.create_category(
        session, household_id=household.id, name=body.name,
        parent_id=body.parent_id, is_system=body.is_system)
    emit(session, household.id, actor, "create", "category", c.id,
         {"name": c.name, "parent_id": c.parent_id})
    return schemas.CategoryOut.model_validate(c)


@router.patch("/{category_id}", response_model=schemas.CategoryOut)
def patch_category(
    category_id: int, body: schemas.CategoryPatch,
    household: HouseholdDep, session: SessionDep, actor: ActorDep,
) -> schemas.CategoryOut:
    c = _get(session, household.id, category_id)
    changed = body.model_dump(exclude_unset=True)
    if "parent_id" in changed:
        new_parent_id = changed.pop("parent_id")
        if new_parent_id is not None:
            _get(session, household.id, new_parent_id)
        cats.reparent(session, c.id, new_parent_id)
        session.refresh(c)
    if "name" in changed:
        c.name = changed["name"]
    if "archived" in changed and changed["archived"] is not None:
        c.archived = changed["archived"]
    emit(session, household.id, actor, "update", "category", c.id, changed)
    return schemas.CategoryOut.model_validate(c)


@router.delete("/{category_id}", status_code=204)
def delete_category(
    category_id: int, household: HouseholdDep,
    session: SessionDep, actor: ActorDep,
) -> None:
    """Categories archive rather than delete — history is preserved."""
    c = _get(session, household.id, category_id)
    c.archived = True
    emit(session, household.id, actor, "archive", "category", c.id)
