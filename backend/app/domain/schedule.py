"""Expected-movement schedule engine (issue #72).

Unifies recurring commitments, payroll runs and manual plans into a
dated expected-income/expenditure stream — the forecast substrate
Epic 7 reports on. Every row traces to its source via
(source_type, source_id, expected_date); re-materialisation deletes
only FUTURE rows for that source then regenerates, so it is idempotent
and never rewrites history.
"""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.domain import goals, outbox, pay_calendar, paydays, payroll_totals, payrun, transfer_rules
from app.domain.accounts import current_movement_account
from app.domain.recurring import advance, occurrences
from app.persistence.models import (
    AmountKind,
    Confidence,
    Employment,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Household,
    PayrollRun,
    PayrollRunStatus,
    RecurringCommitment,
    SavingsGoal,
    ScenarioOp,
    Subscription,
    SubscriptionStatus,
    TransferRule,
)

DEFAULT_HORIZON_DAYS = 365


def _emit_row(
    household_id: int,
    *,
    account_id: int | None,
    expected_date: date,
    amount_pence: int,
    label: str,
    confidence: Confidence,
    source: ExpectedSource,
    source_id: int,
) -> ExpectedMovement:
    return ExpectedMovement(
        household_id=household_id,
        account_id=account_id,
        expected_date=expected_date,
        amount_pence=amount_pence,
        label=label,
        confidence=confidence,
        source_type=source,
        source_id=source_id,
    )


def _drop_future(
    session: Session, source: ExpectedSource, source_id: int, today: date
) -> dict[tuple[date, int | None], ExpectedMovement]:
    """Delete unmatched FUTURE rows for a source; return the matched
    survivors keyed by ``(expected_date, account_id)`` so callers can
    re-price them in place. Matched rows can't be deleted —
    ``expected_match`` FKs to them — and shouldn't be: real money
    already met the obligation they represent."""
    session.execute(
        delete(ExpectedMovement).where(
            ExpectedMovement.source_type == source,
            ExpectedMovement.source_id == source_id,
            ExpectedMovement.expected_date >= today,
            ExpectedMovement.dismissed_at.is_(None),
            ExpectedMovement.id.not_in(select(ExpectedMatch.movement_id)),
        )
    )
    return {
        (m.expected_date, m.account_id): m
        for m in session.execute(
            select(ExpectedMovement).where(
                ExpectedMovement.source_type == source,
                ExpectedMovement.source_id == source_id,
                ExpectedMovement.expected_date >= today,
            )
        ).scalars()
    }


def _upsert(
    session: Session,
    survivors: dict[tuple[date, int | None], ExpectedMovement],
    household_id: int,
    *,
    account_id: int | None,
    expected_date: date,
    amount_pence: int,
    label: str,
    confidence: Confidence,
    source: ExpectedSource,
    source_id: int,
) -> None:
    """Emit an expected row, or re-price the matched survivor for this
    (date, account) so an edited schedule updates the outstanding
    expectation instead of colliding on the unique key."""
    existing = survivors.pop((expected_date, account_id), None)
    if existing is not None:
        existing.amount_pence = amount_pence
        existing.label = label
        existing.confidence = confidence
        return
    session.add(
        _emit_row(
            household_id,
            account_id=account_id,
            expected_date=expected_date,
            amount_pence=amount_pence,
            label=label,
            confidence=confidence,
            source=source,
            source_id=source_id,
        )
    )


def rematerialize_commitment(
    session: Session,
    c: RecurringCommitment,
    *,
    today: date | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Delete+regenerate FUTURE expected rows for one commitment."""
    today = today or date.today()
    horizon = today + timedelta(days=horizon_days)
    survivors = _drop_future(session, ExpectedSource.commitment, c.id, today)
    if not c.active:
        session.flush()
        return 0
    n = 0
    for d in occurrences(c, horizon):
        if d < today:
            continue
        _upsert(
            session,
            survivors,
            c.household_id,
            account_id=c.account_id,
            expected_date=d,
            amount_pence=c.amount_pence or 0,
            label=c.name,
            confidence=(Confidence.estimated if c.formula else Confidence.scheduled),
            source=ExpectedSource.commitment,
            source_id=c.id,
        )
        n += 1
    session.flush()
    return n


def _emit_payroll_estimate_change(session: Session, employment: Employment, pay_date: date,
                                  previous: int | None, current: int, source: str) -> None:
    """Compare known estimates for one dated obligation; first values seed quietly."""
    if previous is None or previous == current:
        return
    outbox.emit(session, employment.household_id, "payroll.estimate_changed",
        entity_kind="employment", entity_id=employment.id,
        payload={"source": source, "status": "estimate", "expected_date": pay_date.isoformat(),
                 "name": employment.employer, "amount_pence": current, "old_pence": previous})


def rematerialize_payroll_run(
    session: Session, run: PayrollRun, *,
    today: date | None = None, previous_estimate: int | None = None,
) -> int:
    """Expected income on the run's pay date — estimated until the run
    is reconciled, scheduled after. Only future/mutable runs emit."""
    today = today or date.today()
    if previous_estimate is None:
        previous_estimate = session.scalar(select(ExpectedMovement.amount_pence).where(
            ExpectedMovement.source_type == ExpectedSource.payroll_run,
            ExpectedMovement.source_id == run.id,
            ExpectedMovement.expected_date == run.pay_date,
            ExpectedMovement.account_id == run.employment.paid_into_account_id,
            ExpectedMovement.confidence == Confidence.estimated))
    survivors = _drop_future(
        session, ExpectedSource.payroll_run, run.id, today)
    if run.status is PayrollRunStatus.corrected \
            or run.pay_date < today:
        session.flush()
        return 0
    actual = payroll_totals.totals_for_run(run).actual_net_pence
    confidence = Confidence.scheduled if actual is not None else Confidence.estimated
    amount = actual if actual is not None else run.net_pence
    if (
        amount == 0
        and run.status is PayrollRunStatus.forecast
        and run.employment.employment_type == "piecework"
    ):
        # Keep the payroll identity for history/reuse, without a fictitious receipt.
        session.flush()
        return 0
    # Reuse the estimate's identity when bank money already matched it.
    # The same applies to a correction replacing a matched prior run.
    superseded = list(
        session.scalars(
            select(ExpectedMovement).where(
                ExpectedMovement.expected_date == run.pay_date,
                # A destination change must not move settled/dismissed history or
                # saved scenario references from the previous account.
                ExpectedMovement.account_id == run.employment.paid_into_account_id,
                (
                    (ExpectedMovement.source_type == ExpectedSource.employment)
                    & (ExpectedMovement.source_id == run.employment_id)
                )
                | (
                    (ExpectedMovement.source_type == ExpectedSource.payroll_run)
                    & (ExpectedMovement.source_id == run.corrects_id)
                ),
            )
        )
    )
    key = (run.pay_date, run.employment.paid_into_account_id)
    canonical = survivors.get(key)
    if canonical is None and superseded:
        canonical = superseded.pop(0)
        canonical.source_type = ExpectedSource.payroll_run
        canonical.source_id = run.id
        canonical.account_id = run.employment.paid_into_account_id
        survivors[key] = canonical
    if previous_estimate is None and canonical is not None \
            and canonical.confidence == Confidence.estimated:
        previous_estimate = canonical.amount_pence
    if canonical is not None:
        for previous in superseded:
            for match in session.scalars(
                select(ExpectedMatch).where(ExpectedMatch.movement_id == previous.id)
            ):
                match.movement_id = canonical.id
            if canonical.dismissed_at is None:
                canonical.dismissed_at = previous.dismissed_at
            session.delete(previous)
        session.flush()
    _upsert(
        session,
        survivors,
        run.employment.household_id,
        account_id=run.employment.paid_into_account_id,
        expected_date=run.pay_date,
        amount_pence=amount,
        label=f"Salary — {run.employment.employer}",
        confidence=confidence,
        source=ExpectedSource.payroll_run,
        source_id=run.id,
    )
    session.flush()
    if confidence == Confidence.estimated:
        _emit_payroll_estimate_change(session, run.employment, run.pay_date,
                                      previous_estimate, amount, "payroll_run")
    return 1


def _employment_net_estimate(session: Session, employment: Employment, pay: date) -> int | None:
    """Standalone regular-pay estimate using the configuration at payday.

    Runs own their actual dates; their overtime, bonuses, adjustments and
    cumulative tax history must never become a recurring salary estimate.
    Missing prerequisites leave the date unestimated.
    """
    from app.domain import payroll as dom
    from app.domain import tax

    try:
        if employment.employment_type == "piecework" or employment.payday_rule is None:
            return None  # Logged shifts feed runs; unknown shifts are not income.
        rule = pay_calendar.parse_rule(employment.payday_rule)
        if rule.kind is paydays.RuleKind.nightly:
            return None  # Annual salary has no supported nightly projection basis.
        periods = 52 if rule.kind.value == "weekly" else 12
        if periods == 52:
            gross = dom.salary_at(session, employment.id, pay) // 52
        else:
            label = pay.replace(day=1)
            # Match the configured nominal month even when its cash date
            # was shifted across the end of the preceding month.
            for offset in (-1, 0, 1):
                index = pay.year * 12 + pay.month - 1 + offset
                candidate = date(index // 12, index % 12 + 1, 1)
                if paydays.pay_date(rule, candidate.year, candidate.month) == pay:
                    label = candidate
                    break
            first = label
            last = date(label.year, label.month, calendar.monthrange(label.year, label.month)[1])
            if (
                employment.salary_policy != "equal_monthly"
                and "cutoff_day" in employment.payday_rule
            ):
                window = pay_calendar.period(
                    pay_calendar.parse_calendar(employment.payday_rule), label.year, label.month
                )
                first, last = window.start, window.cutoff
            first = max(first, employment.start_date or first)
            last = min(last, employment.end_date or last)
            gross = dom.gross_for_period(session, employment.id, first, last)
        pension = payrun.pension_pence_for(employment, gross)
        if employment.deduction_mode == "gross_only":
            return gross - pension
        statutory_date = payrun.statutory_date_for(employment, pay, label if periods == 12 else pay)
        rules = tax.rule_set(session, tax.tax_year_for(statutory_date), employment.jurisdiction)
        ded = tax.compute_period(
            gross_pence=gross,
            tax_code=dom.tax_code_at(session, employment.id, statutory_date),
            params=json.loads(rules.params),
            period=1,
            periods_per_year=periods,
            ni_category=employment.ni_category,
            student_loan_plan=employment.student_loan_plan.value,
            pension_mode=employment.pension_mode,
            pension_pence=pension,
        )
        return ded.net_pence
    except (dom.PayrollError, tax.TaxError, ValueError):
        return None


def _prune_employment_rows(
    session: Session,
    remaining: dict[tuple[date, int | None], ExpectedMovement],
) -> None:
    """Remove obsolete estimates without recycling saved scenario references."""
    matched = set(
        session.scalars(
            select(ExpectedMatch.movement_id).where(
                ExpectedMatch.movement_id.in_([row.id for row in remaining.values()])
            )
        )
    )
    obsolete = [
        row for row in remaining.values() if row.id not in matched and row.dismissed_at is None
    ]
    if obsolete:
        # Scenario references intentionally degrade to no-op when a date or
        # destination changes, rather than targeting a later reused SQLite ID.
        session.execute(
            update(ScenarioOp)
            .where(ScenarioOp.expected_movement_id.in_([row.id for row in obsolete]))
            .values(expected_movement_id=None)
        )
        for row in obsolete:
            session.delete(row)
    session.flush()


def withdraw_employment_estimates(session: Session, employment: Employment, today: date) -> None:
    """Missing calculation evidence must not fall back to guessed salary income."""
    rows = session.scalars(
        select(ExpectedMovement).where(
            ExpectedMovement.source_type == ExpectedSource.employment,
            ExpectedMovement.source_id == employment.id,
            ExpectedMovement.expected_date >= today,
        )
    )
    _prune_employment_rows(session, {(row.expected_date, row.account_id): row for row in rows})


def rematerialize_employment(
    session: Session,
    employment: Employment,
    *,
    today: date | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Projected paydays from the employment's payday rule (issue #454).

    Real runs own their dates — projected rows only fill paydays no
    generated/reconciled run covers, so generating a run replaces the
    estimate seamlessly. Requires ``paid_into_account_id``; without it
    income has no account to land in and nothing is emitted."""
    payrun.flag_outdated_forecasts(session, employment)
    today = today or date.today()
    # Stable identities keep saved scenario remove/shift operations attached
    # across daily/startup repairs. Only obsolete unmatched rows are pruned.
    survivors = {
        (row.expected_date, row.account_id): row
        for row in session.scalars(
            select(ExpectedMovement).where(
                ExpectedMovement.source_type == ExpectedSource.employment,
                ExpectedMovement.source_id == employment.id,
                ExpectedMovement.expected_date >= today,
            )
        )
    }
    if (
        not employment.is_active
        or employment.paid_into_account_id is None
        or employment.payday_rule is None
    ):
        _prune_employment_rows(session, survivors)
        return 0
    try:
        rule = pay_calendar.parse_rule(employment.payday_rule)
    except (ValueError, KeyError, TypeError):
        _prune_employment_rows(session, survivors)
        return 0
    if rule.kind is paydays.RuleKind.nightly:
        _prune_employment_rows(session, survivors)
        return 0
    horizon = today + timedelta(days=horizon_days)
    runs = list(
        session.scalars(
            select(PayrollRun).where(
                PayrollRun.employment_id == employment.id,
                PayrollRun.status != PayrollRunStatus.corrected,
            )
        )
    )
    covered = {run.pay_date for run in runs}
    # A prepared/received payroll period owns its income even while a new
    # payday rule awaits deliberate rebuilding. Do not add a second salary.
    for run in runs:
        try:
            snapshot = json.loads(run.calculation_inputs or "{}")
            label = date.fromisoformat(
                snapshot.get("payroll_month", run.period_end.replace(day=1).isoformat())
            )
        except (ValueError, TypeError, AttributeError):
            snapshot = {}
            label = run.period_end.replace(day=1)
        if run.pay_date >= today and snapshot.get("payday_rule"):
            try:
                old_rule = pay_calendar.parse_rule(snapshot["payday_rule"])
            except (ValueError, TypeError, KeyError):
                _prune_employment_rows(session, survivors)
                return 0  # Preserve known runs until ambiguous periods are reviewed.
            periodic = {paydays.RuleKind.weekly, paydays.RuleKind.nightly}
            if old_rule.kind != rule.kind and (old_rule.kind in periodic or rule.kind in periodic):
                _prune_employment_rows(session, survivors)
                return 0  # Frequency transitions require explicit new periods.
        if rule.kind is paydays.RuleKind.weekly:
            assert rule.weekday is not None
            nominal = run.statutory_pay_date or run.pay_date
            covered.add(nominal - timedelta(days=nominal.weekday()) + timedelta(days=rule.weekday))
        else:
            covered.add(paydays.pay_date(rule, label.year, label.month))
    end = employment.end_date or horizon
    n = 0
    d = today - timedelta(days=1)
    while True:
        d = paydays.next_payday(rule, d)
        if d > min(horizon, end):
            break
        if d in covered or (employment.start_date is not None and d < employment.start_date):
            continue
        estimate = _employment_net_estimate(session, employment, d)
        if estimate is None:
            continue
        previous = survivors.get((d, employment.paid_into_account_id))
        prior_amount = (previous.amount_pence if previous is not None
                        and previous.confidence == Confidence.estimated else None)
        _upsert(session, survivors, employment.household_id,
                account_id=employment.paid_into_account_id,
                expected_date=d, amount_pence=estimate,
                label=f"Salary — {employment.employer}",
                confidence=Confidence.estimated,
                source=ExpectedSource.employment,
                source_id=employment.id)
        _emit_payroll_estimate_change(session, employment, d, prior_amount, estimate, "employment")
        n += 1
    _prune_employment_rows(session, survivors)
    return n


def rematerialize_employments(
    session: Session,
    *,
    today: date | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Repair existing employment forecasts and keep the horizon rolling.

    Include inactive employments so stale unmatched forecasts disappear.
    Runs and matched/history rows retain their existing ownership.
    """
    return sum(
        rematerialize_employment(session, employment, today=today, horizon_days=horizon_days)
        for employment in session.scalars(select(Employment)).all()
    )


def rematerialize_transfer_rule(
    session: Session,
    rule: TransferRule,
    *,
    today: date | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Two legs per occurrence: out on the source account, in on the
    destination — same source_id, distinguished by account_id.
    Fixed amounts are ``scheduled``; income_pct/budget_line are
    ``estimated`` because they depend on data that can still change."""
    today = today or date.today()
    horizon = today + timedelta(days=horizon_days)
    survivors = _drop_future(session, ExpectedSource.transfer_rule, rule.id, today)
    if not rule.active:
        session.flush()
        return 0
    spec = transfer_rules.parse_spec(rule.amount_spec)
    confidence = Confidence.scheduled if spec.kind is AmountKind.fixed else Confidence.estimated
    n = 0
    for d in transfer_rules.occurrences(rule, horizon, today):
        amount = transfer_rules.evaluate(session, rule, d)
        if amount is None:
            continue
        for account_id, pence, suffix in (
            (rule.source_account_id, -amount, "out"),
            (rule.dest_account_id, amount, "in"),
        ):
            _upsert(
                session,
                survivors,
                rule.household_id,
                account_id=account_id,
                expected_date=d,
                amount_pence=pence,
                label=f"{rule.name} ({suffix})",
                confidence=confidence,
                source=ExpectedSource.transfer_rule,
                source_id=rule.id,
            )
            n += 1
    session.flush()
    return n


def rematerialize_budget_derived_rules(
    session: Session, household_id: int, *, today: date | None = None
) -> list[int]:
    """After a budget revision is approved, every active rule whose
    amount derives from a budget line recomputes its future rows.
    Returns the affected rule ids (callers audit the event)."""
    rules = (
        session.execute(
            select(TransferRule).where(
                TransferRule.household_id == household_id, TransferRule.active.is_(True)
            )
        )
        .scalars()
        .all()
    )
    affected: list[int] = []
    for rule in rules:
        if transfer_rules.parse_spec(rule.amount_spec).kind is AmountKind.budget_line:
            rematerialize_transfer_rule(session, rule, today=today)
            affected.append(rule.id)
    return affected


def rematerialize_goal(
    session: Session,
    goal: SavingsGoal,
    *,
    today: date | None = None,
) -> int:
    """Sinking-fund monthly expectations into the pot account —
    `estimated` (the need recomputes as contributions land)."""
    today = today or date.today()
    _drop_future(session, ExpectedSource.savings_goal, goal.id, today)
    if not goal.active:
        session.flush()
        return 0
    n = 0
    for d, pence in goals.sinking_schedule(session, goal, today=today):
        if d < today:
            continue
        session.add(
            _emit_row(
                goal.household_id,
                account_id=goal.account_id,
                expected_date=d,
                amount_pence=pence,
                label=f"{goal.name} contribution",
                confidence=Confidence.estimated,
                source=ExpectedSource.savings_goal,
                source_id=goal.id,
            )
        )
        n += 1
    session.flush()
    return n


def rematerialize_subscription(
    session: Session,
    sub: Subscription,
    *,
    today: date | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Renewal expectations out to the horizon — scheduled confidence
    (a register entry is an asserted obligation). Cancelled subs emit
    nothing: history stays, the forecast forgets."""
    today = today or date.today()
    horizon = today + timedelta(days=horizon_days)
    _drop_future(session, ExpectedSource.subscription, sub.id, today)
    if sub.status is SubscriptionStatus.cancelled:
        session.flush()
        return 0
    n = 0
    d = sub.next_renewal_date
    while d <= horizon:
        if d >= today:
            session.add(
                _emit_row(
                    sub.household_id,
                    account_id=sub.account_id,
                    expected_date=d,
                    amount_pence=-sub.expected_pence,
                    label=f"{sub.payee} subscription",
                    confidence=Confidence.scheduled,
                    source=ExpectedSource.subscription,
                    source_id=sub.id,
                )
            )
            n += 1
        d = advance(d, sub.frequency, sub.anchor_day)
    session.flush()
    return n


def materialize_all(
    session: Session,
    household: Household,
    *,
    today: date | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Regenerate every source's future rows for a household."""
    today = today or date.today()
    n = 0
    for c in (
        session.execute(
            select(RecurringCommitment).where(
                RecurringCommitment.household_id == household.id,
                RecurringCommitment.active.is_(True),
            )
        )
        .scalars()
        .all()
    ):
        n += rematerialize_commitment(session, c, today=today, horizon_days=horizon_days)
    for r in (
        session.execute(
            select(PayrollRun).where(PayrollRun.employment.has(household_id=household.id))
        )
        .scalars()
        .all()
    ):
        n += rematerialize_payroll_run(session, r, today=today)
    for e in (
        session.execute(
            select(Employment).where(
                Employment.household_id == household.id, Employment.is_active.is_(True)
            )
        )
        .scalars()
        .all()
    ):
        n += rematerialize_employment(session, e, today=today, horizon_days=horizon_days)
    for rule in (
        session.execute(
            select(TransferRule).where(
                TransferRule.household_id == household.id, TransferRule.active.is_(True)
            )
        )
        .scalars()
        .all()
    ):
        n += rematerialize_transfer_rule(session, rule, today=today, horizon_days=horizon_days)
    for g in (
        session.execute(
            select(SavingsGoal).where(
                SavingsGoal.household_id == household.id, SavingsGoal.active.is_(True)
            )
        )
        .scalars()
        .all()
    ):
        n += rematerialize_goal(session, g, today=today)
    for s in (
        session.execute(
            select(Subscription).where(
                Subscription.household_id == household.id,
                Subscription.status == SubscriptionStatus.active,
            )
        )
        .scalars()
        .all()
    ):
        n += rematerialize_subscription(session, s, today=today)
    return n


def add_manual(
    session: Session,
    household_id: int,
    *,
    account_id: int | None,
    expected_date: date,
    amount_pence: int,
    label: str,
) -> ExpectedMovement:
    """A user-entered plan — its own source."""
    row = ExpectedMovement(
        household_id=household_id,
        account_id=account_id,
        expected_date=expected_date,
        amount_pence=amount_pence,
        label=label,
        confidence=Confidence.scheduled,
        source_type=ExpectedSource.manual,
        source_id=0,
    )
    session.add(row)
    session.flush()
    row.source_id = row.id  # self-referential traceability
    session.flush()
    return row


def stream(
    session: Session, household_id: int, *, start: date, end: date, account_id: int | None = None
) -> list[ExpectedMovement]:
    stmt = select(ExpectedMovement).where(
        current_movement_account(),
        ExpectedMovement.household_id == household_id,
        ExpectedMovement.expected_date >= start,
        ExpectedMovement.expected_date <= end,
    )
    if account_id is not None:
        stmt = stmt.where(ExpectedMovement.account_id == account_id)
    return list(
        session.execute(stmt.order_by(ExpectedMovement.expected_date, ExpectedMovement.id))
        .scalars()
        .all()
    )
