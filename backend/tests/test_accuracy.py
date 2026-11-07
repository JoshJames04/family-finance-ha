"""Issue #112 — worst-forecast-category ranking."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.domain import budgets as dom
from app.persistence.models import (
    Account,
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    Category,
    Household,
    Transaction,
)


def _acct(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="A", type="joint",
                opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _cat(session: Session, household: Household, name: str) -> Category:
    c = Category(household_id=household.id, name=name)
    session.add(c)
    session.flush()
    return c


def _budget(session: Session, household: Household, name: str,
            start: date, end: date) -> Budget:
    b = Budget(household_id=household.id, name=name,
               period_type=BudgetPeriodType.monthly, start_date=start,
               end_date=end, status=BudgetStatus.approved)
    session.add(b)
    session.flush()
    return b


def _line(session: Session, budget: Budget, cat: Category,
          original: int, revised: int | None = None) -> BudgetLine:
    ln = BudgetLine(budget_id=budget.id, category_id=cat.id,
                    side=BudgetLineSide.expense,
                    original_pence=original, revised_pence=revised)
    session.add(ln)
    session.flush()
    return ln


def _spend(session: Session, acct: Account, cat: Category,
           amount: int, d: date) -> None:
    session.add(Transaction(account_id=acct.id, date=d,
                            amount_pence=-amount, payee="P",
                            category_id=cat.id))
    session.flush()


@pytest.fixture()
def seeded(session: Session, household: Household):
    """Jan: Food 10000 planned/5000 actual (50%), Travel 10000/9000
    (90%). Feb: Food revised 8000/8000 actual → eff-acc 100%,
    orig-acc 80%."""
    a = _acct(session, household)
    food = _cat(session, household, "Food")
    travel = _cat(session, household, "Travel")
    jan = _budget(session, household, "Jan",
                  date(2025, 1, 1), date(2025, 1, 31))
    _line(session, jan, food, 10_000)
    _line(session, jan, travel, 10_000)
    _spend(session, a, food, 5_000, date(2025, 1, 15))
    _spend(session, a, travel, 9_000, date(2025, 1, 20))
    feb = _budget(session, household, "Feb",
                  date(2025, 2, 1), date(2025, 2, 28))
    _line(session, feb, food, 10_000, revised=8_000)
    _spend(session, a, food, 8_000, date(2025, 2, 15))
    budgets = [jan, feb]
    return {"food": food, "travel": travel, "budgets": budgets}


class TestRanking:
    def test_golden_figures(
            self, session: Session, seeded, household) -> None:
        rows = dom.accuracy_ranking(session, seeded["budgets"])
        by_id = {r["category_id"]: r for r in rows}
        food = by_id[seeded["food"].id]
        # (50 + 100)/2 = 75.0  (50 + 80)/2 = 65.0  Δ = +10.0
        assert food["mean_accuracy_pct"] == 75.0
        assert food["mean_original_accuracy_pct"] == 65.0
        assert food["revision_delta_pct"] == 10.0
        assert food["periods"] == 2
        travel = by_id[seeded["travel"].id]
        assert travel["mean_accuracy_pct"] == 90.0
        assert travel["periods"] == 1

    def test_worst_first_ordering(
            self, session: Session, seeded) -> None:
        rows = dom.accuracy_ranking(session, seeded["budgets"])
        assert rows[0]["category_id"] == seeded["food"].id
        assert rows[-1]["category_id"] == seeded["travel"].id

    def test_empty_budgets_empty_ranking(
            self, session: Session, household: Household) -> None:
        assert dom.accuracy_ranking(session, []) == []

    def test_visibility_filtered_caller_side(
            self, session: Session, seeded,
            household: Household) -> None:
        """A private budget excluded by the caller disappears from the
        ranking — its lines don't pollute the shared means."""
        rows_all = dom.accuracy_ranking(session, seeded["budgets"])
        jan_only = dom.accuracy_ranking(
            session, [seeded["budgets"][0]])
        food = {r["category_id"]: r for r in jan_only}[
            seeded["food"].id]
        assert food["mean_accuracy_pct"] == 50.0
        assert food["periods"] == 1
        assert len(rows_all) == 2

