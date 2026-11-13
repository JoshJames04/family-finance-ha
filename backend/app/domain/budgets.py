"""Budget lifecycle and actuals (issue #57).

draft → approved → locked. Approval freezes line originals; revisions
are only legal while approved; locked budgets (explicitly or via a
locked reporting period covering the range) are read-only.

Overlapping budgets coexist freely — actuals are computed per-budget by
date range, so a transaction is attributed to each budget independently
and never double-counted *within* a budget.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain import contributions, outbox
from app.domain.reporting import (
    expense_leg,
    external_category_amounts,
    income_leg,
)
from app.persistence.models import (
    Account,
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    ExpectedMovement,
    ExpectedSource,
    ReportingPeriod,
    Transaction,
    TransactionStatus,
)


class BudgetError(ValueError):
    """Domain rule violation on budget operations."""


def period_covers_locked(session: Session, household_id: int,
                         start: date, end: date) -> bool:
    """True when a locked reporting period fully covers [start, end]."""
    return session.execute(
        select(func.count(ReportingPeriod.id)).where(
            ReportingPeriod.household_id == household_id,
            ReportingPeriod.locked.is_(True),
            ReportingPeriod.start_date <= start,
            ReportingPeriod.end_date >= end)
    ).scalar_one() > 0


def is_read_only(session: Session, budget: Budget) -> bool:
    return (budget.status == BudgetStatus.locked
            or period_covers_locked(session, budget.household_id,
                                    budget.start_date, budget.end_date))


def _assert_mutable(session: Session, budget: Budget) -> None:
    if is_read_only(session, budget):
        raise BudgetError("budget is locked")


def create_budget(
    session: Session, household_id: int, name: str,
    period_type: BudgetPeriodType, start: date, end: date,
    owner_profile_id: int | None = None,
) -> Budget:
    if end < start:
        raise BudgetError("end_date must be on/after start_date")
    b = Budget(household_id=household_id,
               owner_profile_id=owner_profile_id, name=name,
               period_type=period_type, start_date=start, end_date=end,
               status=BudgetStatus.draft)
    session.add(b)
    session.flush()
    return b


def add_line(session: Session, budget: Budget, category_id: int,
             side: BudgetLineSide, amount_pence: int) -> BudgetLine:
    _assert_mutable(session, budget)
    if budget.status != BudgetStatus.draft:
        raise BudgetError("lines can only be added while draft")
    if amount_pence < 0:
        raise BudgetError("line amounts are unsigned; side carries sign")
    line = BudgetLine(budget=budget, category_id=category_id,
                      side=side, original_pence=amount_pence)
    session.add(line)
    session.flush()
    return line


def approve(session: Session, budget: Budget) -> Budget:
    _assert_mutable(session, budget)
    if budget.status != BudgetStatus.draft:
        raise BudgetError("only draft budgets can be approved")
    if not budget.lines:
        raise BudgetError("cannot approve a budget with no lines")
    budget.status = BudgetStatus.approved
    session.flush()
    from app.domain.financial_events import evaluate_budgets
    evaluate_budgets(session, budget.household_id)
    outbox.emit(session, budget.household_id, "budget.approved", entity_kind="budget",
                entity_id=budget.id, payload={"status": budget.status.value},
                dedup_key=f"budget:{budget.id}:approved")
    return budget


def revise_line(session: Session, line: BudgetLine,
                amount_pence: int, reason: str) -> BudgetLine:
    budget = line.budget
    _assert_mutable(session, budget)
    if budget.status != BudgetStatus.approved:
        raise BudgetError("revisions only while approved")
    if amount_pence < 0:
        raise BudgetError("line amounts are unsigned")
    if not reason.strip():
        raise BudgetError("revisions require a reason")
    from app.domain.financial_events import evaluate_budgets
    evaluate_budgets(session, budget.household_id)
    line.revised_pence = amount_pence
    line.revision_reason = reason.strip()
    session.flush()
    evaluate_budgets(session, budget.household_id)
    return line


def line_analysis(actual_pence: int,
                  line: BudgetLine) -> dict[str, object]:
    """Variance + accuracy per ADR-0007's honest-budgeting rule.

    variance  = effective - actual (positive → under budget)
    accuracy  = 100 - |effective - actual| / max(|eff|, |act|, 1) * 100
    Both reported against original and revised so the planner can see
    whether revisions improved accuracy."""
    eff = effective_pence(line)
    denom = max(abs(eff), abs(actual_pence), 1)
    accuracy = round(100 - abs(eff - actual_pence) / denom * 100, 2)
    orig_denom = max(abs(line.original_pence), abs(actual_pence), 1)
    orig_accuracy = round(
        100 - abs(line.original_pence - actual_pence)
        / orig_denom * 100, 2)
    return {
        "original_pence": line.original_pence,
        "revised_pence": line.revised_pence,
        "revision_reason": line.revision_reason,
        "effective_pence": eff,
        "actual_pence": actual_pence,
        "variance_pence": eff - actual_pence,
        "accuracy_pct": accuracy,
        "original_accuracy_pct": orig_accuracy,
    }


def lock(session: Session, budget: Budget) -> Budget:
    if budget.status != BudgetStatus.approved:
        raise BudgetError("only approved budgets can be locked")
    budget.status = BudgetStatus.locked
    session.flush()
    outbox.emit(session, budget.household_id, "budget.locked", entity_kind="budget",
                entity_id=budget.id, payload={"status": budget.status.value},
                dedup_key=f"budget:{budget.id}:locked")
    return budget


def effective_pence(line: BudgetLine) -> int:
    """Revised value if present, else the frozen original."""
    return (line.revised_pence if line.revised_pence is not None
            else line.original_pence)


def actuals_for_budget(session: Session, budget: Budget,
                       account_ids: list[int] | None = None, *,
                       include_private: bool = False,
                       start: date | None = None,
                       end: date | None = None) -> dict[int, int]:
    """Per-line actual totals (expense lines: spend as positive pence;
    income lines: income as positive pence). Overlapping budgets each
    see the same underlying transactions — attribution is per-budget,
    so nothing is double-counted inside a single budget."""
    out: dict[int, int] = {}
    ea = external_category_amounts(
        Account.household_id == budget.household_id,
        *([Account.owner_profile_id == budget.owner_profile_id]
          if budget.owner_profile_id is not None or not include_private else []),
        Transaction.date >= (start or budget.start_date),
        Transaction.date <= (end or budget.end_date),
        *([Account.id.in_(account_ids)] if account_ids is not None else []),
        Transaction.status != TransactionStatus.void,
        Transaction.transfer_id.is_(None))
    for line in budget.lines:
        q = select(func.coalesce(func.sum(ea.c.amount_pence), 0)).where(
            ea.c.category_id == line.category_id)
        if line.side == BudgetLineSide.expense:
            q = q.where(expense_leg(ea))
        else:
            q = q.where(income_leg(ea))
        total = int(session.execute(q).scalar_one())
        # expense lines measure outflow (negative ledger values);
        # income lines measure inflow (positive values)
        out[line.id] = -total if line.side == BudgetLineSide.expense \
            else total
    return out


def bills_remaining(session: Session, budget: Budget,
                    account_ids: list[int] | None = None, *,
                    include_private: bool = False,
                    start: date | None = None,
                    end: date | None = None,
                    ) -> tuple[int, int]:
    """Unpaid scheduled outgoings left in the budget period (issue
    #415). Commitment/subscription expected movements in the window
    that no real transaction has matched — a past-due unposted one is
    overdue, not gone, so it still counts. Member budgets see only
    movements on the owner's accounts; shared budgets see shared accounts
    and accountless obligations. All explicitly includes private accounts.
    Returns ``(pence, count)`` with pence positive."""
    conds = [
        ExpectedMovement.household_id == budget.household_id,
        ExpectedMovement.source_type.in_(
            [ExpectedSource.commitment, ExpectedSource.subscription]),
        ExpectedMovement.expected_date >= (start or budget.start_date),
        ExpectedMovement.expected_date <= (end or budget.end_date),
        ExpectedMovement.amount_pence < 0,
        ExpectedMovement.dismissed_at.is_(None)]
    if budget.owner_profile_id is not None or not include_private:
        scoped_accounts = ExpectedMovement.account_id.in_(
            select(Account.id).where(
                Account.owner_profile_id == budget.owner_profile_id))
        conds.append(or_(scoped_accounts, ExpectedMovement.account_id.is_(None))
                     if budget.owner_profile_id is None else scoped_accounts)
    if account_ids is not None:
        conds.append(or_(ExpectedMovement.account_id.in_(account_ids),
                         ExpectedMovement.account_id.is_(None))
                     if budget.owner_profile_id is None
                     else ExpectedMovement.account_id.in_(account_ids))
    residuals = [contributions.status(session, m).outstanding_pence
                 for m in session.scalars(select(ExpectedMovement).where(*conds))]
    return sum(residuals), sum(value > 0 for value in residuals)


def spending_summary(session: Session, budget: Budget,
                     account_ids: list[int] | None = None,
                     as_of: date | None = None, *,
                     include_private: bool = False,
                     start: date | None = None,
                     end: date | None = None) -> dict[str, Any]:
    """As-of net expense flow with an exact itemised bridge.

    Outflows count even without a plan/category. Explicitly linked refunds
    offset spending; other positive rows are receipts. Economic direction
    cannot change with account scope or budget plan composition.
    Opening stock, transfers and void rows are excluded. Per-line actuals
    retain full-period semantics for historical analysis and rollover.
    """
    through = min(end or budget.end_date, as_of or date.today())
    amounts = external_category_amounts(
        Account.household_id == budget.household_id,
        *([Account.owner_profile_id == budget.owner_profile_id]
          if budget.owner_profile_id is not None or not include_private else []),
        *([Account.id.in_(account_ids)] if account_ids is not None else []),
        Transaction.date >= (start or budget.start_date), Transaction.date <= through)
    effective = session.execute(select(amounts).order_by(
        amounts.c.date, amounts.c.transaction_id, amounts.c.category_id)).all()
    covered = {line.category_id for line in budget.lines
               if line.side == BudgetLineSide.expense}
    items: list[dict[str, Any]] = [{"transaction_id": row.transaction_id, "date": row.date,
              "category_id": row.category_id, "amount_pence": -row.amount_pence}
             for row in effective
             if row.amount_pence < 0 or (row.amount_pence > 0 and row.refund_of_id is not None)]
    spent = sum(int(item["amount_pence"]) for item in items)
    budget_covered = sum(int(item["amount_pence"]) for item in items
                         if item["category_id"] in covered)
    return {"as_of": through, "spent_pence": spent,
            "budget_covered_pence": budget_covered,
            "unallocated_pence": spent - budget_covered,
            "uncategorised_pence": sum(int(item["amount_pence"])
                for item in items if item["category_id"] is None),
            "income_received_pence": sum(row.amount_pence for row in effective
                if row.amount_pence > 0 and row.refund_of_id is None),
            "spend_items": items}



# ---------- templates + copy-forward (issue #59) ----------------------
from app.persistence.models import (  # noqa: E402
    BudgetTemplate,
    BudgetTemplateLine,
    Category,
)


def create_template(session: Session, household_id: int,
                    name: str) -> BudgetTemplate:
    t = BudgetTemplate(household_id=household_id, name=name)
    session.add(t)
    session.flush()
    return t


def add_template_line(session: Session, template: BudgetTemplate,
                      category_id: int, side: BudgetLineSide,
                      amount_pence: int) -> BudgetTemplateLine:
    if amount_pence < 0:
        raise BudgetError("template amounts are unsigned")
    line = BudgetTemplateLine(template=template,
                              category_id=category_id, side=side,
                              amount_pence=amount_pence)
    session.add(line)
    session.flush()
    return line


def update_template_line(session: Session, line: BudgetTemplateLine,
                         amount_pence: int) -> BudgetTemplateLine:
    """Editing a template never mutates budgets made from it — the
    application copies values at apply-time, not references."""
    if amount_pence < 0:
        raise BudgetError("template amounts are unsigned")
    line.amount_pence = amount_pence
    session.flush()
    return line


def apply_template(
    session: Session, budget: Budget, template: BudgetTemplate
) -> tuple[list[BudgetLine], list[str]]:
    """Copy template lines into a draft budget. Returns (created,
    warnings) — warnings name categories skipped (archived) or already
    present (partial application allowed)."""
    _assert_mutable(session, budget)
    if budget.status != BudgetStatus.draft:
        raise BudgetError("templates apply only to draft budgets")
    existing = {line.category_id for line in budget.lines}
    created, warnings = [], []
    for tl in template.lines:
        cat = session.get(Category, tl.category_id)
        name = cat.name if cat else f"#{tl.category_id}"
        if cat is None or cat.household_id != budget.household_id:
            warnings.append(f"category {name} unavailable")
            continue
        if cat.archived:
            warnings.append(f"category {name} archived — skipped")
            continue
        if tl.category_id in existing:
            warnings.append(f"category {name} already a line — skipped")
            continue
        created.append(add_line(session, budget, tl.category_id,
                                tl.side, tl.amount_pence))
    if budget.from_template_id is None and created:
        budget.from_template_id = template.id
    return created, warnings


def copy_forward(
    session: Session, source: Budget, name: str,
    start: date, end: date,
    period_type: BudgetPeriodType | None = None,
) -> tuple[Budget, list[str]]:
    """Clone a budget into a new draft for a later period.

    original→original, revised is NOT carried (a fresh planning cycle
    starts clean); provenance via copied_from_id. Archived-category
    lines clone too (they keep historical meaning) with a warning."""
    new = create_budget(
        session, source.household_id, name,
        period_type or source.period_type, start, end,
        owner_profile_id=source.owner_profile_id)
    new.copied_from_id = source.id
    warnings = []
    for line in source.lines:
        cat = session.get(Category, line.category_id)
        if cat is not None and cat.archived:
            warnings.append(f"category {cat.name} archived — cloned")
        add_line(session, new, line.category_id, line.side,
                 line.original_pence)
    return new, warnings


# ---------- rollover (issue #60) ---------------------------------------
def apply_rollover(
    session: Session, source: Budget, target: Budget
) -> list[dict[str, object]]:
    """Carry per-line surplus/deficit into the target budget's revised.

    carry = source.effective - source.actual  (underspend → positive).
    Target revised becomes original + carry. Only source lines opted in
    via ``rollover`` participate. The source is never mutated (safe
    across locked boundaries); each application writes an audit-visible
    reason and returns the carry events for the caller to audit."""
    if source.status == BudgetStatus.draft:
        raise BudgetError("cannot roll over from a draft budget")
    if target.status == BudgetStatus.locked or is_read_only(
            session, target):
        raise BudgetError("target budget is locked")
    actuals = actuals_for_budget(session, source)
    src_lines = {sl.category_id: sl for sl in source.lines}
    events: list[dict[str, object]] = []
    for tl in target.lines:
        sl = src_lines.get(tl.category_id)
        if sl is None or not sl.rollover:
            continue
        carry = effective_pence(sl) - actuals.get(sl.id, 0)
        tl.revised_pence = tl.original_pence + carry
        tl.revision_reason = (
            f"rollover from budget {source.id} "
            f"(carry {carry:+d}p)")
        events.append({
            "source_budget_id": source.id,
            "target_budget_id": target.id,
            "line_id": tl.id, "category_id": tl.category_id,
            "carry_pence": carry,
        })
    session.flush()
    return events


# ---------- reporting (issue #62) ---------------------------------------
def line_status(variance_pence: int, effective_pence: int,
                warn_pct: float = 10.0, over_pct: float = 20.0) -> str:
    """Traffic-light status by configurable overspend thresholds.

    overspend % = max(0, actual - effective) / effective * 100.
    Zero-budget lines with spend are 'over'."""
    if effective_pence <= 0:
        return "over" if variance_pence < 0 else "on_track"
    overspend_pct = max(0, -variance_pence) / effective_pence * 100
    if overspend_pct > over_pct:
        return "over"
    if overspend_pct > warn_pct:
        return "watch"
    return "on_track"


def report_for_budget(
    session: Session, budget: Budget,
    warn_pct: float = 10.0, over_pct: float = 20.0,
) -> list[dict[str, object]]:
    """Per-line triple-value report: original/revised/actual/variance/
    variance_pct/accuracy/status. Golden-tested computations."""
    actuals = actuals_for_budget(session, budget)
    rows: list[dict[str, object]] = []
    for line in budget.lines:
        a = line_analysis(actuals.get(line.id, 0), line)
        eff = int(str(a["effective_pence"]))
        var = int(str(a["variance_pence"]))
        a["variance_pct"] = (
            round(var / eff * 100, 2) if eff else
            (0.0 if var >= 0 else -100.0))
        a["status"] = line_status(var, eff, warn_pct, over_pct)
        a["line_id"] = line.id
        a["category_id"] = line.category_id
        rows.append(a)
    return rows


def accuracy_history(
    session: Session, household_id: int, category_id: int
) -> list[dict[str, object]]:
    """How close forecasts land over time for one category — every
    budget containing a line for it, oldest → newest."""
    budgets = session.execute(
        select(Budget).where(Budget.household_id == household_id)
        .order_by(Budget.start_date)).scalars().all()
    out: list[dict[str, object]] = []
    for b in budgets:
        for line in b.lines:
            if line.category_id != category_id:
                continue
            actual = actuals_for_budget(session, b).get(line.id, 0)
            a = line_analysis(actual, line)
            out.append({
                "budget_id": b.id, "name": b.name,
                "start_date": b.start_date.isoformat(),
                "end_date": b.end_date.isoformat(),
                "actual_pence": actual,
                "effective_pence": a["effective_pence"],
                "variance_pence": a["variance_pence"],
                "accuracy_pct": a["accuracy_pct"],
                "original_accuracy_pct": a["original_accuracy_pct"],
            })
    return out


def accuracy_ranking(
    session: Session, budgets: list[Budget],
) -> list[dict[str, object]]:
    """Per-category mean forecast accuracy across ``budgets``, worst
    first — the systematically mis-forecast categories (issue #112).

    The caller passes the budgets *visible* to the requesting profile;
    private budgets never leak into a shared ranking.
    """
    acc: dict[int, list[float]] = {}
    orig: dict[int, list[float]] = {}
    for b in budgets:
        actuals = actuals_for_budget(session, b)
        for line in b.lines:
            a = line_analysis(actuals.get(line.id, 0), line)
            acc.setdefault(line.category_id, []).append(
                float(str(a["accuracy_pct"])))
            orig.setdefault(line.category_id, []).append(
                float(str(a["original_accuracy_pct"])))
    names = {c.id: c.name for c in session.execute(
        select(Category).where(
            Category.household_id == budgets[0].household_id)
    ).scalars()} if budgets else {}
    rows = [{
        "category_id": cid,
        "name": names.get(cid, f"Category {cid}"),
        "periods": len(vals),
        "mean_accuracy_pct": round(sum(vals) / len(vals), 2),
        "mean_original_accuracy_pct": round(
            sum(orig[cid]) / len(orig[cid]), 2),
    } for cid, vals in acc.items()]
    for r in rows:
        r["revision_delta_pct"] = round(
            float(str(r["mean_accuracy_pct"]))
            - float(str(r["mean_original_accuracy_pct"])), 2)
    rows.sort(key=lambda r: (
        float(str(r["mean_accuracy_pct"])), int(str(r["category_id"]))))
    return rows
