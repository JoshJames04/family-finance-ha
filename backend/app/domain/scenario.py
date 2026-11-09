"""Issue #76 — what-if scenario layer.

A scenario is a set of ops (add / remove / shift) applied to a COPY
of the expected-movement stream — real ``ExpectedMovement`` rows are
never read for write and never modified. ``run()`` projects the same
account twice over the same window and diffs the two series, so the
answer is deterministic: same rows + same ops → identical diff.

Payday anchors come from the BASELINE stream: removing a payroll
movement in a scenario must not silently redefine "payday" and hide
the effect it was meant to show.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.accounts import current_balance
from app.domain.projection import (
    MAX_HORIZON_DAYS,
    Projection,
    _aggregate,
    _assemble,
    _movement_rows,
    _next_payday,
)
from app.persistence.models import (
    Account,
    ExpectedMovement,
    ExpectedSource,
    Scenario,
    ScenarioOp,
    ScenarioOpKind,
)


class ScenarioError(ValueError):
    """Domain rejection → 422 at the API edge."""


@dataclass(frozen=True)
class PaydayDelta:
    date: date
    baseline_balance_pence: int
    scenario_balance_pence: int
    delta_pence: int


@dataclass(frozen=True)
class ScenarioDiff:
    """Where the scenario changed the story vs baseline."""
    min_balance_changed: bool
    baseline_min_balance_pence: int
    baseline_min_balance_date: date | None
    scenario_min_balance_pence: int
    scenario_min_balance_date: date | None
    min_balance_delta_pence: int
    safe_to_spend_delta_pence: int
    paydays: list[PaydayDelta]


@dataclass(frozen=True)
class ScenarioRun:
    scenario_id: int
    account_id: int
    days: int
    baseline: Projection
    projection: Projection
    diff: ScenarioDiff


def create_scenario(session: Session, household_id: int,
                    name: str) -> Scenario:
    s = Scenario(household_id=household_id, name=name)
    session.add(s)
    session.flush()
    return s


def list_scenarios(session: Session, household_id: int
                   ) -> list[Scenario]:
    return list(session.scalars(
        select(Scenario).where(Scenario.household_id == household_id)
        .order_by(Scenario.name)))


def get_scenario(session: Session, household_id: int,
                 scenario_id: int) -> Scenario | None:
    return session.scalar(
        select(Scenario).where(Scenario.id == scenario_id,
                               Scenario.household_id == household_id))


def delete_scenario(session: Session, scenario: Scenario) -> None:
    session.delete(scenario)


def add_op(session: Session, scenario: Scenario, *,
           kind: ScenarioOpKind,
           expected_movement_id: int | None = None,
           account_id: int | None = None,
           expected_date: date | None = None,
           amount_pence: int | None = None,
           label: str | None = None) -> ScenarioOp:
    """Validate kind-specific payloads, then persist. ``remove`` /
    ``shift`` must point at a real movement in the same household —
    enforced once at write time so ``run()`` can treat references as
    degrade-to-no-op (movements are re-materialised)."""
    if kind is ScenarioOpKind.add:
        missing = [f for f, v in (
            ("account_id", account_id), ("expected_date", expected_date),
            ("amount_pence", amount_pence), ("label", label))
            if v is None]
        if missing:
            raise ScenarioError(
                f"add op requires {', '.join(missing)}")
    else:
        if expected_movement_id is None:
            raise ScenarioError(f"{kind} op requires expected_movement_id")
        exists = session.scalar(
            select(ExpectedMovement.id).where(
                ExpectedMovement.id == expected_movement_id,
                ExpectedMovement.household_id == scenario.household_id))
        if exists is None:
            raise ScenarioError(
                f"expected_movement {expected_movement_id} not found")
        if kind is ScenarioOpKind.shift and expected_date is None:
            raise ScenarioError("shift op requires expected_date")
    op = ScenarioOp(
        scenario_id=scenario.id, kind=kind,
        expected_movement_id=expected_movement_id,
        account_id=account_id, expected_date=expected_date,
        amount_pence=amount_pence, label=label)
    session.add(op)
    session.flush()
    return op


def _apply_ops(rows: list[tuple[int, date, int]],
               ops: list[ScenarioOp], account_id: int,
               start: date, end: date) -> list[tuple[date, int]]:
    """Fold ops onto a copied row stream; real rows untouched.

    Ops are ordered by creation so add→remove→shift compose in the
    sequence the user defined them."""
    removed = {o.expected_movement_id for o in ops
               if o.kind is ScenarioOpKind.remove}
    shifted = {o.expected_movement_id: o.expected_date for o in ops
               if o.kind is ScenarioOpKind.shift}
    out: list[tuple[date, int]] = []
    for mid, d, amt in rows:
        if mid in removed:
            continue
        new_date = shifted.get(mid, d)
        out.append((new_date if new_date is not None else d, amt))
    for o in ops:
        if (o.kind is ScenarioOpKind.add
                and o.account_id == account_id):
            assert o.expected_date is not None
            assert o.amount_pence is not None
            out.append((o.expected_date, o.amount_pence))
    # Shifts can push a movement outside the window — clip so
    # out-of-window items affect neither the series nor committed.
    return [(d, a) for d, a in out if start < d <= end]


def _payday_dates(session: Session, account_id: int, start: date,
                  end: date) -> list[date]:
    """All baseline payroll dates in (start, end] — the comparison
    anchors for payday-balance deltas."""
    return list(session.scalars(
        select(ExpectedMovement.expected_date)
        .where(ExpectedMovement.account_id == account_id,
               ExpectedMovement.source_type == ExpectedSource.payroll_run,
               ExpectedMovement.amount_pence > 0,
               ExpectedMovement.expected_date > start,
               ExpectedMovement.expected_date <= end,
               ExpectedMovement.dismissed_at.is_(None))
        .order_by(ExpectedMovement.expected_date)))


def _balances_by_date(proj: Projection) -> dict[date, int]:
    return {p.date: p.balance_pence for p in proj.days}


def run(session: Session, scenario: Scenario, account: Account,
        days: int, from_date: date | None = None) -> ScenarioRun:
    """Baseline vs scenario projection over the same window."""
    days = max(1, min(days, MAX_HORIZON_DAYS))
    today = from_date or date.today()
    end = today + timedelta(days=days)

    rows = _movement_rows(session, account.id, today, end)
    base_rows = [(d, amt) for _, d, amt in rows]
    scen_rows = _apply_ops(rows, list(scenario.ops), account.id,
                           today, end)

    balance = current_balance(session, account.id)
    payday = _next_payday(session, account.id, today, end)

    base_net, base_neg = _aggregate(base_rows)
    scen_net, scen_neg = _aggregate(scen_rows)
    baseline = _assemble(account, balance, base_net, base_neg,
                         payday, today, end)
    projected = _assemble(account, balance, scen_net, scen_neg,
                          payday, today, end)

    base_bal = _balances_by_date(baseline)
    scen_bal = _balances_by_date(projected)
    paydays = [
        PaydayDelta(d, base_bal[d], scen_bal[d],
                    scen_bal[d] - base_bal[d])
        for d in _payday_dates(session, account.id, today, end)]
    diff = ScenarioDiff(
        min_balance_changed=(
            baseline.min_balance_date != projected.min_balance_date
            or baseline.min_balance_pence != projected.min_balance_pence),
        baseline_min_balance_pence=baseline.min_balance_pence,
        baseline_min_balance_date=baseline.min_balance_date,
        scenario_min_balance_pence=projected.min_balance_pence,
        scenario_min_balance_date=projected.min_balance_date,
        min_balance_delta_pence=(
            projected.min_balance_pence - baseline.min_balance_pence),
        safe_to_spend_delta_pence=(
            projected.safe_to_spend_pence - baseline.safe_to_spend_pence),
        paydays=paydays)
    return ScenarioRun(scenario.id, account.id, days,
                       baseline, projected, diff)
