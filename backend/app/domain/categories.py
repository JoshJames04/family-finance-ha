"""Category domain rules — hierarchy integrity the DB can't express."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import Category


class CategoryCycleError(ValueError):
    pass


def descendants_of(session: Session, category_id: int) -> set[int]:
    """All descendant ids of a category (BFS — depth is small in practice)."""
    seen: set[int] = set()
    frontier = [category_id]
    while frontier:
        rows = session.execute(
            select(Category.id).where(Category.parent_id.in_(frontier))
        ).scalars().all()
        frontier = [r for r in rows if r not in seen]
        seen.update(frontier)
    return seen


def assert_no_cycle(session: Session, category_id: int,
                    new_parent_id: int | None) -> None:
    """Reject reparenting that would create a cycle."""
    if new_parent_id is None:
        return
    if new_parent_id == category_id:
        raise CategoryCycleError("category cannot be its own parent")
    if new_parent_id in descendants_of(session, category_id):
        raise CategoryCycleError("new parent is a descendant of the category")


def create_category(session: Session, *, household_id: int, name: str,
                    parent_id: int | None = None,
                    is_system: bool = False) -> Category:
    cat = Category(household_id=household_id, name=name, parent_id=parent_id,
                   is_system=is_system)
    session.add(cat)
    session.flush()
    return cat


def reparent(session: Session, category_id: int,
             new_parent_id: int | None) -> Category:
    assert_no_cycle(session, category_id, new_parent_id)
    cat = session.get(Category, category_id)
    if cat is None:
        raise KeyError(f"category {category_id} not found")
    cat.parent_id = new_parent_id
    session.flush()
    return cat
