"""Canonical financial transitions, with transactional per-entity prior state.

Snapshots seed quietly on existing installs. Later changes append to the durable
outbox in the caller's transaction; rollback removes both state and events. This
is financial entity state, never an inference from summary counts.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.domain import budgets, energy, goals, loans, outbox, seasonal
from app.persistence.models import (
    Account,
    AppMeta,
    Budget,
    BudgetLineSide,
    BudgetStatus,
    Employment,
    EnergyConfig,
    ExpectedMovement,
    ExpectedSource,
    Loan,
    PayrollRun,
    Payslip,
    RecurringCommitment,
    Repayment,
    SeasonalEvent,
    Transaction,
)


def _observe(
    session: Session, key: str, value: dict[str, Any]
) -> tuple[dict[str, Any] | None, int] | None:
    row = session.get(AppMeta, key)
    stored = json.loads(row.value) if row is not None else {}
    previous = stored.get("value")
    if previous == value:
        return None
    revision = int(stored.get("revision", 0)) + 1
    serialised = json.dumps(
        {"revision": revision, "value": value}, sort_keys=True, separators=(",", ":")
    )
    if row is None:
        session.add(AppMeta(key=key, value=serialised))
    else:
        row.value = serialised
    return previous, revision


def evaluate_budgets(session: Session, household_id: int, *, today: date | None = None) -> None:
    today = today or date.today()
    session.flush()
    for budget in session.scalars(
        select(Budget).where(
            Budget.household_id == household_id,
            Budget.status.in_([BudgetStatus.approved, BudgetStatus.locked]),
        )
    ):
        # Automation's shared view never aggregates private account flow.
        accounts = list(
            session.scalars(
                select(Account.id).where(
                    Account.household_id == household_id,
                    Account.owner_profile_id == budget.owner_profile_id,
                )
            )
        )
        summary = budgets.spending_summary(session, budget, accounts, as_of=today)
        planned = sum(
            line.effective_pence for line in budget.lines if line.side == BudgetLineSide.expense
        )
        spent = int(summary["spent_pence"])
        remaining = planned - spent
        current = {"owner": budget.owner_profile_id, "planned": planned, "spent": spent}
        key = f"finance:budget:{budget.id}"
        change = _observe(session, key, current)
        if change is None or change[0] is None or change[0].get("owner") != current["owner"]:
            continue
        previous, revision = change
        assert previous is not None
        payload: dict[str, object] = {
            "source": "budget",
            "status": "over" if remaining < 0 else "under",
            "date": today.isoformat(),
            "planned_pence": planned,
            "spent_pence": spent,
            "remaining_pence": remaining,
            "old_remaining_pence": int(previous["planned"]) - int(previous["spent"]),
            "old_usage_pct": (round(int(previous["spent"]) * 100 / int(previous["planned"]), 2)
                              if int(previous["planned"]) else None),
            "usage_pct": round(spent * 100 / planned, 2) if planned else None,
        }
        kinds = ["budget.usage"]
        if int(previous["planned"]) - int(previous["spent"]) != remaining:
            kinds.append("budget.remaining_changed")
        if int(previous["planned"]) - int(previous["spent"]) >= 0 and remaining < 0:
            kinds.append("budget.over")
        for kind in kinds:
            outbox.emit(
                session,
                household_id,
                kind,
                entity_kind="budget",
                entity_id=budget.id,
                payload=payload,
                dedup_key=f"{key}:{revision}:{kind}",
            )


def evaluate_seasonal(session: Session, household_id: int, *, today: date | None = None) -> None:
    today = today or date.today()
    session.flush()
    for event in session.scalars(
        select(SeasonalEvent).where(SeasonalEvent.household_id == household_id)
    ):
        seasonal.emit_approaching(session, event, today=today)
        totals = seasonal.totals(session, event)
        current = {
            "owner": event.owner_profile_id,
            "budget": event.budget_pence,
            "actual": totals.actual_pence,
            "committed": totals.committed_pence,
            "planned": totals.planned_pence,
        }
        key = f"finance:seasonal:{event.id}"
        change = _observe(session, key, current)
        if change is None or change[0] is None or change[0].get("owner") != current["owner"]:
            continue
        outbox.emit(
            session,
            household_id,
            "seasonal.variance",
            entity_kind="seasonal_event",
            entity_id=event.id,
            payload={
                "source": "seasonal",
                "status": "over" if totals.remaining_pence < 0 else "under",
                "date": today.isoformat(),
                "budget_pence": event.budget_pence,
                "actual_pence": totals.actual_pence,
                "committed_pence": totals.committed_pence,
                "remaining_pence": totals.remaining_pence,
                "remaining_after_commitments_pence": totals.remaining_after_commitments_pence,
            },
            dedup_key=f"{key}:{change[1]}",
        )


def evaluate_energy(session: Session, household_id: int, *, today: date | None = None) -> None:
    today = today or date.today()
    session.flush()
    config = session.scalar(select(EnergyConfig).where(EnergyConfig.household_id == household_id))
    if config is None or config.budget_category_id is None:
        return
    try:
        report = energy.month_report(session, household_id, today, shared_only=True)
    except energy.EnergyError:
        return  # No tariff is unknown, never a fabricated zero estimate.
    current = {
        "budget": report.budget_pence,
        "estimate": report.estimate.total_pence,
        "projected": report.projected_total_pence,
        "missing": report.estimate.missing_days,
    }
    key = f"finance:energy:{config.id}:{today:%Y-%m}"
    change = _observe(session, key, current)
    if change is None or change[0] is None:
        return
    outbox.emit(
        session,
        household_id,
        "energy.variance",
        entity_kind="energy_config",
        entity_id=config.id,
        payload={
            "source": "energy",
            "status": "estimate_incomplete" if report.estimate.missing_days else "estimate",
            "date": today.isoformat(),
            "budget_pence": report.budget_pence,
            "estimate_pence": report.estimate.total_pence,
            "variance_pence": report.variance_pence,
            "projected_total_pence": report.projected_total_pence,
            "projected_variance_pence": report.projected_variance_pence,
            "missing_days": report.estimate.missing_days,
        },
        dedup_key=f"{key}:{change[1]}",
    )


def evaluate_payslips(session: Session, household_id: int, *, today: date | None = None) -> None:
    today = today or date.today()
    session.flush()
    for slip_id, status in session.execute(
        select(Payslip.id, Payslip.status).where(Payslip.household_id == household_id)
    ):
        key = f"finance:payslip:{slip_id}"
        change = _observe(session, key, {"status": status.value})
        if change is None or change[0] is None:
            continue
        outbox.emit(
            session,
            household_id,
            "payslip.state_changed",
            entity_kind="payslip",
            entity_id=slip_id,
            payload={
                "source": "payslip",
                "old_status": change[0]["status"],
                "new_status": status.value,
                "status": status.value,
                "date": today.isoformat(),
            },
            dedup_key=f"{key}:{change[1]}",
        )


def evaluate_paydays(session: Session, household_id: int, *, today: date | None = None) -> None:
    today = today or date.today()
    session.flush()
    occurrences: dict[tuple[int, date], ExpectedMovement] = {}
    for movement in session.scalars(
        select(ExpectedMovement).where(
            ExpectedMovement.household_id == household_id,
            ExpectedMovement.expected_date >= today,
            ExpectedMovement.expected_date <= today + timedelta(days=3),
            ExpectedMovement.amount_pence > 0,
            ExpectedMovement.dismissed_at.is_(None),
            ExpectedMovement.source_type.in_(
                [ExpectedSource.employment, ExpectedSource.payroll_run]
            ),
        )
    ):
        employment = None
        if movement.source_type == ExpectedSource.employment:
            employment = session.get(Employment, movement.source_id)
        else:
            run = session.get(PayrollRun, movement.source_id)
            if run is not None:
                employment = run.employment
        if (
            employment is not None
            and employment.household_id == household_id
            and employment.is_active
            and (employment.end_date is None or employment.end_date >= movement.expected_date)
        ):
            occurrences[(employment.id, movement.expected_date)] = movement
    for employment_id, due in occurrences:
        outbox.emit(
            session,
            household_id,
            "payroll.approaching",
            entity_kind="employment",
            entity_id=employment_id,
            payload={
                "source": "payroll",
                "status": "approaching",
                "date": today.isoformat(),
                "expected_date": due.isoformat(),
                "lead_days": 3,
            },
            dedup_key=f"payroll:approaching:{employment_id}:{due}:3",
        )


def record_backup_status(
    session: Session, household_id: int, *, succeeded: bool, today: date | None = None
) -> None:
    today = today or date.today()
    key = f"finance:backup:{household_id}"
    change = _observe(session, key, {"status": "completed" if succeeded else "failed"})
    if not succeeded and change is not None:
        outbox.emit(
            session,
            household_id,
            "backup.failed",
            entity_kind="backup",
            payload={"source": "backup", "status": "failed", "date": today.isoformat()},
            dedup_key=f"{key}:{change[1]}",
        )


@contextmanager
def observe_loan_evidence(session: Session, household_id: int, *transaction_ids: int,
                          loan: Loan | None = None) -> Iterator[None]:
    """Record any balance transition while a transaction ceases to qualify."""
    affected = [loan] if loan is not None else []
    if transaction_ids:
        current, adjacent = aliased(Transaction), aliased(Transaction)
        family = select(Transaction.id.label("id")).where(
            Transaction.id.in_(transaction_ids)).cte(recursive=True)
        family = family.union(select(adjacent.id).select_from(family).join(
            current, current.id == family.c.id).join(adjacent, or_(
                adjacent.corrects_id == current.id, current.corrects_id == adjacent.id)))
        affected.extend(session.scalars(select(Loan).where(
            Loan.household_id == household_id, Loan.id.in_(select(Repayment.loan_id).where(
                Repayment.transaction_id.in_(select(family.c.id)))))).all())
    participants = list(dict.fromkeys(affected))
    for row in participants:
        evaluate_loan(session, row)
    yield
    session.flush()
    for row in participants:
        evaluate_loan(session, row)


def evaluate_loan(session: Session, loan: Loan, *, today: date | None = None) -> None:
    """Record the canonical dated balance, never infer a bank payment or due date."""
    today = today or date.today()
    session.flush()
    balance = loans.outstanding(session, loan, today)
    current = {"owner": loan.owner_profile_id, "active": loan.active,
               "outstanding": balance}
    key = f"finance:loan:{loan.id}"
    change = _observe(session, key, current)
    if change is None or change[0] is None or not loan.active:
        return
    previous, revision = change
    assert previous is not None
    if previous.get("owner") != current["owner"] or not previous.get("active"):
        return
    old = int(previous["outstanding"])
    if old == balance:
        return
    payload: dict[str, object] = {
        "source": "loan", "status": "fully_repaid" if balance == 0 else "outstanding",
        "date": today.isoformat(), "direction": loan.direction.value,
        "name": loan.party, "old_outstanding_pence": old,
        "outstanding_pence": balance, "currency": "GBP",
        "_scope_owner_profile_id": loan.owner_profile_id,
    }
    kinds = ["loan.balance_changed"]
    if old > 0 and balance == 0:
        kinds.append("loan.fully_repaid")
    for kind in kinds:
        outbox.emit(session, loan.household_id, kind, entity_kind="loan", entity_id=loan.id,
                    payload=payload, dedup_key=f"{key}:{revision}:{kind}")


def seed_bill_end(session: Session, bill: RecurringCommitment) -> None:
    """New records need a durable baseline before any scheduler/offline gap."""
    _observe(session, f"finance:bill_end:{bill.household_id}:{bill.id}", {"ended": False})


def _bill_end_conditions(bill: RecurringCommitment, today: date) -> tuple[bool, bool]:
    ended_by_date = bill.end_date is not None and bill.end_date < today
    ended_by_count = (bill.occurrence_count is not None
                      and bill.generated_count >= bill.occurrence_count
                      and bill.last_generated is not None and bill.last_generated < today)
    return ended_by_date, ended_by_count


def reset_active_bill_end(session: Session, bill: RecurringCommitment) -> None:
    """A committed schedule edit can reopen an ended bill before the next wake."""
    if not any(_bill_end_conditions(bill, date.today())):
        seed_bill_end(session, bill)


def evaluate_bill_ends(session: Session, household_id: int, *, today: date | None = None) -> None:
    """Observe explicit schedule end boundaries, never future generation or payment.

    Both materialisation and settlement update last_generated/generated_count.
    First observation seeds existing records quietly; later scheduler wakes catch
    a real date crossing, including after downtime, in the same transaction.
    """
    today = today or date.today()
    session.flush()
    for bill in session.scalars(select(RecurringCommitment).where(
            RecurringCommitment.household_id == household_id)):
        ended_by_date, ended_by_count = _bill_end_conditions(bill, today)
        ended = ended_by_date or ended_by_count
        key = f"finance:bill_end:{household_id}:{bill.id}"
        change = _observe(session, key, {"ended": ended})
        if change is None or change[0] is None or not ended:
            continue
        last_date = min(boundary for boundary in (
            bill.end_date if ended_by_date else None,
            bill.last_generated if ended_by_count else None) if boundary is not None)
        account = session.get(Account, bill.account_id)
        outbox.emit(session, household_id, "bill.ended",
            entity_kind="commitment", entity_id=bill.id,
            payload={"_scope_owner_profile_id": account.owner_profile_id if account else None,
                     "status": "ended", "date": today.isoformat(),
                     "expected_date": last_date.isoformat() if last_date else None,
                     "source": ("configured_end" if ended_by_date and last_date == bill.end_date
                                else "final_occurrence"),
                     "name": bill.name, "amount_pence": bill.amount_pence},
            dedup_key=f"{key}:{change[1]}")


def evaluate_household(session: Session, household_id: int, *, today: date | None = None) -> None:
    evaluate_bill_ends(session, household_id, today=today)
    evaluate_budgets(session, household_id, today=today)
    evaluate_seasonal(session, household_id, today=today)
    evaluate_energy(session, household_id, today=today)
    evaluate_payslips(session, household_id, today=today)
    goals.evaluate_automation(session, household_id, today=today)
    evaluate_paydays(session, household_id, today=today)
    for loan in session.scalars(select(Loan).where(Loan.household_id == household_id)):
        evaluate_loan(session, loan, today=today)
