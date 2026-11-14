"""Issue #90 — internal movement is never income or expense.

Two layers:
1. A static gate that AST-scans ``app/domain`` for every
   ``func.sum(Transaction.amount_pence)`` aggregate and requires the
   enclosing function to apply ``external_only`` or state its own
   ``transfer_id`` predicate (splits-helper callers choose between
   ``is_(None)`` and ``isnot(None)`` per report) — unless the
   function is explicitly allowlisted with a documented reason.
2. Behavioural proof that a transfer-heavy dataset produces zero
   income/expense impact on every reporting surface.
"""
from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.domain import budgets as budgets_dom
from app.domain import refunds, schedule, transfer_rules
from app.persistence.models import (
    Account,
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetStatus,
    Category,
    Frequency,
    Household,
    Transaction,
    TransactionStatus,
    Transfer,
    TransferRule,
)

TODAY = date(2025, 4, 10)
DOMAIN = Path(__file__).parents[1] / "app" / "domain"

# Aggregates where transfer legs are legitimately included — each entry
# must be justified in app/domain/reporting.py's docstring. (Python's
# builtin sum() sites like transfers.legs_balance are not SQL
# aggregates and never appear here.)
# contributed_pence deliberately sums INTERNAL transfer legs (goal
# contributions are transfers by design); repaid_pence aggregates
# Repayment rows, not Transaction rows — the guard doesn't apply.
# _monthly_net feeds balance accumulation — legs are in-flight money
# either side of a month boundary (same basis as net_worth_at).
# _reconcile_opening restamps openings so ledger == provider balance;
# it must match current_balance's all-legs basis to be correct.
# _opening_funds is ledger stock strictly before a budget's displayed window.
ALLOWLIST = {
    "current_balance",
    "contributed_pence",
    "repaid_pence",
    "_monthly_net",
    "net_worth_at",
    "_reconcile_opening",
    "_opening_funds",
}


def _sum_sites(path: Path) -> list[tuple[str, int]]:
    """(enclosing function, line) for each amount_pence sum."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sites: list[tuple[str, int]] = []

    def visit(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if (isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "sum"
                    and child.args
                    and isinstance(child.args[0], ast.Attribute)
                    and child.args[0].attr == "amount_pence"):
                sites.append((fn or child.func.attr, child.lineno))
            visit(child, name)

    visit(tree, "<module>")
    return sites


class TestStaticContract:
    def test_every_aggregate_uses_the_guard(self) -> None:
        violations: list[str] = []
        for path in sorted(DOMAIN.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for fn, line in _sum_sites(path):
                if fn in ALLOWLIST:
                    continue
                # the enclosing function must apply external_only —
                # or state its own transfer_id predicate (splits
                # rollups pass is_(None)/isnot(None) via wheres)
                seg = source.splitlines()
                body = "\n".join(seg[max(0, line - 60):line + 60])
                if "external_only" not in body \
                        and "external_category_amounts" not in body \
                        and "transfer_id" not in body:
                    violations.append(f"{path.name}:{line} ({fn})")
        assert not violations, (
            "amount_pence aggregates missing external_only/"
            "transfer_id guard: " + ", ".join(violations))

    def test_allowlist_entries_exist(self) -> None:
        """Allowlisted functions must actually exist — no dead names."""
        found: set[str] = set()
        for path in DOMAIN.glob("*.py"):
            for fn, _ in _sum_sites(path):
                if fn in ALLOWLIST:
                    found.add(fn)
        assert found == ALLOWLIST


@pytest.fixture()
def two_accounts(session: Session, household: Household
                 ) -> tuple[Account, Account]:
    a = Account(household_id=household.id, name="Personal",
                type="personal", opening_date=date(2025, 1, 1))
    b = Account(household_id=household.id, name="Joint",
                type="joint", opening_date=date(2025, 1, 1))
    session.add_all([a, b])
    session.flush()
    return a, b


def _transfer_legs(session: Session, household: Household,
                   src: Account, dst: Account,
                   pence: int, on: date, category_id: int | None = None,
                   ) -> None:
    """Simulate what transfers.create produces — categorised legs."""
    t = Transfer(household_id=household.id)
    session.add(t)
    session.flush()
    session.add(Transaction(
        account_id=src.id, date=on, amount_pence=-pence, payee="Move",
        category_id=category_id, transfer_id=t.id,
        status=TransactionStatus.cleared, source="manual"))
    session.add(Transaction(
        account_id=dst.id, date=on, amount_pence=pence, payee="Move",
        category_id=category_id, transfer_id=t.id,
        status=TransactionStatus.cleared, source="manual"))
    session.flush()


class TestBudgetActuals:
    def test_transfer_legs_do_not_inflate_actuals(
            self, session: Session, household: Household,
            two_accounts: tuple[Account, Account]) -> None:
        src, dst = two_accounts
        cat = Category(household_id=household.id, name="Food")
        session.add(cat)
        session.flush()
        b = Budget(household_id=household.id, name="April",
                   period_type="monthly",
                   start_date=date(2025, 4, 1),
                   end_date=date(2025, 4, 30),
                   status=BudgetStatus.approved)
        session.add(b)
        session.flush()
        line = BudgetLine(budget_id=b.id, category_id=cat.id,
                          side=BudgetLineSide.expense,
                          original_pence=100_000)
        session.add(line)
        session.flush()
        # real spend £250 + £500 moved between own accounts, both
        # categorised "Food" — only the real spend may count
        session.add(Transaction(
            account_id=src.id, date=date(2025, 4, 5),
            amount_pence=-25_000, payee="Tesco", category_id=cat.id,
            status=TransactionStatus.cleared, source="manual"))
        _transfer_legs(session, household, src, dst, 50_000,
                       date(2025, 4, 6), category_id=cat.id)
        session.flush()
        actuals = budgets_dom.actuals_for_budget(session, b)
        assert actuals[line.id] == 25_000

    def test_transfer_heavy_dataset_zero_impact(
            self, session: Session, household: Household,
            two_accounts: tuple[Account, Account]) -> None:
        """AC: a synthetic transfer-heavy dataset yields zero
        income/expense on reporting surfaces."""
        src, dst = two_accounts
        cat = Category(household_id=household.id, name="Moves")
        session.add(cat)
        session.flush()
        for i in range(20):  # lots of internal shuffling, one category
            _transfer_legs(session, household, src, dst, 10_000 + i,
                           date(2025, 4, 1 + (i % 25)),
                           category_id=cat.id)
        spend = refunds.category_spend(
            session, cat.id, date(2025, 4, 1), date(2025, 4, 30))
        assert spend == 0


class TestIncomePctRule:
    def test_inbound_transfer_not_income(
            self, session: Session, household: Household,
            two_accounts: tuple[Account, Account]) -> None:
        src, dst = two_accounts
        r = TransferRule(
            household_id=household.id, name="Half salary",
            source_account_id=src.id, dest_account_id=dst.id,
            amount_spec='{"kind": "income_pct", "bps": 5000}',
            frequency=Frequency.monthly, day_of_month=1,
            start_date=date(2025, 1, 1), active=True)
        session.add(r)
        # £3,000 real income + £9,000 moved in from another own
        # account — only the £3,000 counts toward "income"
        session.add(Transaction(
            account_id=src.id, date=date(2025, 3, 15),
            amount_pence=300_000, payee="Employer",
            status=TransactionStatus.cleared, source="manual"))
        _transfer_legs(session, household, dst, src, 900_000,
                       date(2025, 3, 20))
        session.flush()
        assert transfer_rules.evaluate(
            session, r, date(2025, 4, 1)) == 150_000

    def test_expected_legs_excluded_from_materialised_amount(
            self, session: Session, household: Household,
            two_accounts: tuple[Account, Account]) -> None:
        """End-to-end: materialised expected amounts ignore transfers."""
        src, dst = two_accounts
        r = TransferRule(
            household_id=household.id, name="Sweep",
            source_account_id=src.id, dest_account_id=dst.id,
            amount_spec='{"kind": "income_pct", "bps": 10000}',
            frequency=Frequency.monthly, day_of_month=1,
            start_date=date(2025, 1, 1), active=True)
        session.add(r)
        session.add(Transaction(
            account_id=src.id, date=date(2025, 3, 10),
            amount_pence=200_000, payee="Salary",
            status=TransactionStatus.cleared, source="manual"))
        _transfer_legs(session, household, dst, src, 500_000,
                       date(2025, 3, 12))
        session.flush()
        schedule.rematerialize_transfer_rule(session, r, today=TODAY)
        from sqlalchemy import select

        from app.persistence.models import ExpectedMovement
        # 100% of prior-period income — the £500k inbound transfer must
        # not appear in any materialised in-leg amount
        in_amounts = [m.amount_pence for m in session.execute(
            select(ExpectedMovement)).scalars()
            if m.account_id == dst.id]
        assert in_amounts
        assert all(a <= 200_000 for a in in_amounts)
