"""Core schema: constraints, hierarchy rules, account types."""

from datetime import date

import pytest
import sqlalchemy.exc

from app.domain.categories import (
    CategoryCycleError,
    create_category,
    reparent,
)
from app.persistence.models import (
    Account,
    AccountType,
    Role,
    UserProfile,
)


def test_household_and_profile(household, session) -> None:
    p = UserProfile(household_id=household.id, display_name="Bob",
                    ha_user_id="ha-1", role=Role.admin)
    session.add(p)
    session.flush()
    assert p.id and p.is_active


def test_duplicate_ha_user_rejected(household, session) -> None:
    session.add(UserProfile(household_id=household.id, display_name="A",
                            ha_user_id="ha-1"))
    session.flush()
    session.add(UserProfile(household_id=household.id, display_name="B",
                            ha_user_id="ha-1"))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        session.flush()


def test_account_types_cover_workbook_arrangements(household, session) -> None:
    """Every account flavour the workbook models is representable."""
    for i, t in enumerate(AccountType):
        session.add(Account(household_id=household.id, name=f"a{i}",
                            type=t, opening_date=date(2024, 1, 1)))
    session.flush()
    assert session.query(Account).count() == len(AccountType)


def test_shared_vs_owned_account(household, session) -> None:
    owner = UserProfile(household_id=household.id, display_name="Bob")
    session.add(owner)
    session.flush()
    shared = Account(household_id=household.id, name="Joint", type=AccountType.joint,
                     opening_date=date(2024, 1, 1))
    personal = Account(household_id=household.id, name="Mine",
                       type=AccountType.personal, owner_profile_id=owner.id,
                       opening_date=date(2024, 1, 1))
    session.add_all([shared, personal])
    session.flush()
    assert shared.owner_profile_id is None
    assert personal.owner_profile_id == owner.id


def test_category_hierarchy_and_cycle_prevention(household, session) -> None:
    parent = create_category(session, household_id=household.id,
                             name="Household Bills")
    child = create_category(session, household_id=household.id,
                            name="Energy", parent_id=parent.id)
    grandchild = create_category(session, household_id=household.id,
                                 name="Electricity", parent_id=child.id)

    # parent under its own grandchild = cycle (child is still a descendant)
    with pytest.raises(CategoryCycleError):
        reparent(session, parent.id, grandchild.id)
    with pytest.raises(CategoryCycleError):
        reparent(session, parent.id, parent.id)
    reparent(session, child.id, None)  # promote to root: fine
    # after promotion, parent has no descendants — parenting it under the
    # grandchild is legal (no cycle)
    reparent(session, parent.id, grandchild.id)


def test_sibling_name_uniqueness(household, session) -> None:
    parent = create_category(session, household_id=household.id, name="P")
    create_category(session, household_id=household.id, name="Dup",
                    parent_id=parent.id)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        create_category(session, household_id=household.id, name="Dup",
                        parent_id=parent.id)
        session.flush()
