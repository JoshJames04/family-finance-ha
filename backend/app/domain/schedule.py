"""Expected-movement schedule engine (issue #72).

Unifies recurring commitments, payroll runs and manual plans into a
dated expected-income/expenditure stream — the forecast substrate
Epic 7 reports on. Every row traces to its source via
(source_type, source_id, expected_date); re-materialisation deletes
only FUTURE rows for that source then regenerates, so it is idempotent
and never rewrites history.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain import goals, pay_calendar, paydays, payroll_totals, payrun, transfer_rules
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
    Subscription,
    SubscriptionStatus,
    TransferRule,
)

DEFAULT_HORIZON_DAYS = 365


def _emit_row(household_id: int, *, account_id: int | None,
              expected_date: date, amount_pence: int, label: str,
              confidence: Confidence, source: ExpectedSource,
              source_id: int) -> ExpectedMovement:
    return ExpectedMovement(
        household_id=household_id, account_id=account_id,
        expected_date=expected_date, amount_pence=amount_pence,
        label=label, confidence=confidence, source_type=source,
        source_id=source_id)


def _drop_future(session: Session, source: ExpectedSource,
                 source_id: int, today: date
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
            ExpectedMovement.id.not_in(
                select(ExpectedMatch.movement_id))))
    return {(m.expected_date, m.account_id): m
            for m in session.execute(
                select(ExpectedMovement).where(
                    ExpectedMovement.source_type == source,
                    ExpectedMovement.source_id == source_id,
                    ExpectedMovement.expected_date >= today)).scalars()}


def _upsert(session: Session,
            survivors: dict[tuple[date, int | None], ExpectedMovement],
            household_id: int, *, account_id: int | None,
            expected_date: date, amount_pence: int, label: str,
            confidence: Confidence, source: ExpectedSource,
            source_id: int) -> None:
    """Emit an expected row, or re-price the matched survivor for this
    (date, account) so an edited schedule updates the outstanding
    expectation instead of colliding on the unique key."""
    existing = survivors.pop((expected_date, account_id), None)
    if existing is not None:
        existing.amount_pence = amount_pence
        existing.label = label
        existing.confidence = confidence
        return
    session.add(_emit_row(
        household_id, account_id=account_id, expected_date=expected_date,
        amount_pence=amount_pence, label=label, confidence=confidence,
        source=source, source_id=source_id))


def rematerialize_commitment(
    session: Session, c: RecurringCommitment, *,
    today: date | None = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Delete+regenerate FUTURE expected rows for one commitment."""
    today = today or date.today()
    horizon = today + timedelta(days=horizon_days)
    survivors = _drop_future(
        session, ExpectedSource.commitment, c.id, today)
    if not c.active:
        session.flush()
        return 0
    n = 0
    for d in occurrences(c, horizon):
        if d < today:
            continue
        _upsert(session, survivors, c.household_id,
                account_id=c.account_id, expected_date=d,
                amount_pence=c.amount_pence or 0, label=c.name,
                confidence=(Confidence.estimated if c.formula
                            else Confidence.scheduled),
                source=ExpectedSource.commitment, source_id=c.id)
        n += 1
    session.flush()
    return n


def rematerialize_payroll_run(
    session: Session, run: PayrollRun, *,
    today: date | None = None,
) -> int:
    """Expected income on the run's pay date — estimated until the run
    is reconciled, scheduled after. Only future/mutable runs emit."""
    today = today or date.today()
    survivors = _drop_future(
        session, ExpectedSource.payroll_run, run.id, today)
    if run.status is PayrollRunStatus.corrected \
            or run.pay_date < today:
        session.flush()
        return 0
    confidence = (Confidence.scheduled
                  if run.status is PayrollRunStatus.reconciled
                  else Confidence.estimated)
    amount = payroll_totals.totals_for_run(run).actual_net_pence \
        if run.status is PayrollRunStatus.reconciled else run.net_pence
    if amount is None:   # unreachable: reconciled ⇒ actual is set
        amount = run.net_pence
    _upsert(
        session, survivors, run.employment.household_id,
        account_id=run.employment.paid_into_account_id,
        expected_date=run.pay_date, amount_pence=amount,
        label=f"Salary — {run.employment.employer}",
        confidence=confidence, source=ExpectedSource.payroll_run,
        source_id=run.id)
    session.flush()
    return 1


def _employment_net_estimate(session: Session,
                             employment: Employment,
                             pay: date) -> int | None:
    """Net-pay estimate for a projected payday (issue #454).

    Latest non-corrected run's net is the best estimate — it already
    carries the member's real deduction profile. With no runs at all,
    compute a standalone month through the tax engine (mid-year
    cumulative-averaging proxy); ``None`` when neither is possible —
    an absent estimate is honest, a wrong one isn't."""
    last = session.scalar(
        select(PayrollRun).where(
            PayrollRun.employment_id == employment.id,
            PayrollRun.status != PayrollRunStatus.corrected)
        .order_by(PayrollRun.pay_date.desc(),
                  PayrollRun.id.desc()).limit(1))
    if last is not None:
        return last.net_pence
    try:
        from app.domain import payroll as dom
        from app.domain import tax
        annual = dom.salary_at(session, employment.id, pay)
        if employment.deduction_mode == "gross_only":
            return annual // 12
        rules = tax.rule_set(
            session, tax.tax_year_for(pay), employment.jurisdiction)
        ded = tax.compute_period(
            gross_pence=annual // 12,
            tax_code=dom.tax_code_at(session, employment.id, pay),
            params=json.loads(rules.params), period=6,
            periods_per_year=12, ytd_taxable_gross_pence=0,
            ytd_tax_pence=0, ni_category=employment.ni_category,
            student_loan_plan=employment.student_loan_plan.value,
            pension_mode=employment.pension_mode,
            pension_pence=payrun.pension_pence_for(
                employment, annual // 12))
        return ded.net_pence
    except Exception:
        return None


def rematerialize_employment(
    session: Session, employment: Employment, *,
    today: date | None = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Projected paydays from the employment's payday rule (issue #454).

    Real runs own their dates — projected rows only fill paydays no
    generated/reconciled run covers, so generating a run replaces the
    estimate seamlessly. Requires ``paid_into_account_id``; without it
    income has no account to land in and nothing is emitted."""
    today = today or date.today()
    survivors = _drop_future(
        session, ExpectedSource.employment, employment.id, today)
    if not employment.is_active \
            or employment.paid_into_account_id is None \
            or employment.payday_rule is None:
        session.flush()
        return 0
    try:
        rule = pay_calendar.parse_rule(employment.payday_rule)
    except (ValueError, KeyError, TypeError):
        session.flush()
        return 0
    horizon = today + timedelta(days=horizon_days)
    covered = {r.pay_date for r in session.scalars(
        select(PayrollRun).where(
            PayrollRun.employment_id == employment.id,
            PayrollRun.pay_date >= today,
            PayrollRun.status != PayrollRunStatus.corrected))}
    end = employment.end_date or horizon
    estimate = _employment_net_estimate(session, employment, today)
    if estimate is None:
        session.flush()
        return 0
    n = 0
    d = today - timedelta(days=1)
    while True:
        d = paydays.next_payday(rule, d)
        if d > min(horizon, end):
            break
        if d in covered:
            continue
        _upsert(session, survivors, employment.household_id,
                account_id=employment.paid_into_account_id,
                expected_date=d, amount_pence=estimate,
                label=f"Salary — {employment.employer}",
                confidence=Confidence.estimated,
                source=ExpectedSource.employment,
                source_id=employment.id)
        n += 1
    session.flush()
    return n


def rematerialize_transfer_rule(
    session: Session, rule: TransferRule, *,
    today: date | None = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Two legs per occurrence: out on the source account, in on the
    destination — same source_id, distinguished by account_id.
    Fixed amounts are ``scheduled``; income_pct/budget_line are
    ``estimated`` because they depend on data that can still change."""
    today = today or date.today()
    horizon = today + timedelta(days=horizon_days)
    survivors = _drop_future(
        session, ExpectedSource.transfer_rule, rule.id, today)
    if not rule.active:
        session.flush()
        return 0
    spec = transfer_rules.parse_spec(rule.amount_spec)
    confidence = (Confidence.scheduled if spec.kind is AmountKind.fixed
                  else Confidence.estimated)
    n = 0
    for d in transfer_rules.occurrences(rule, horizon, today):
        amount = transfer_rules.evaluate(session, rule, d)
        if amount is None:
            continue
        for account_id, pence, suffix in (
                (rule.source_account_id, -amount, "out"),
                (rule.dest_account_id, amount, "in")):
            _upsert(session, survivors, rule.household_id,
                    account_id=account_id, expected_date=d,
                    amount_pence=pence, label=f"{rule.name} ({suffix})",
                    confidence=confidence,
                    source=ExpectedSource.transfer_rule,
                    source_id=rule.id)
            n += 1
    session.flush()
    return n


def rematerialize_budget_derived_rules(
    session: Session, household_id: int, *,
    today: date | None = None) -> list[int]:
    """After a budget revision is approved, every active rule whose
    amount derives from a budget line recomputes its future rows.
    Returns the affected rule ids (callers audit the event)."""
    rules = session.execute(
        select(TransferRule).where(
            TransferRule.household_id == household_id,
            TransferRule.active.is_(True))).scalars().all()
    affected: list[int] = []
    for rule in rules:
        if transfer_rules.parse_spec(
                rule.amount_spec).kind is AmountKind.budget_line:
            rematerialize_transfer_rule(session, rule, today=today)
            affected.append(rule.id)
    return affected


def rematerialize_goal(
    session: Session, goal: SavingsGoal, *,
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
        session.add(_emit_row(
            goal.household_id, account_id=goal.account_id,
            expected_date=d, amount_pence=pence,
            label=f"{goal.name} contribution",
            confidence=Confidence.estimated,
            source=ExpectedSource.savings_goal, source_id=goal.id))
        n += 1
    session.flush()
    return n


def rematerialize_subscription(
    session: Session, sub: Subscription, *,
    today: date | None = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
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
            session.add(_emit_row(
                sub.household_id, account_id=sub.account_id,
                expected_date=d, amount_pence=-sub.expected_pence,
                label=f"{sub.payee} subscription",
                confidence=Confidence.scheduled,
                source=ExpectedSource.subscription, source_id=sub.id))
            n += 1
        d = advance(d, sub.frequency, sub.anchor_day)
    session.flush()
    return n


def materialize_all(
    session: Session, household: Household, *,
    today: date | None = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Regenerate every source's future rows for a household."""
    today = today or date.today()
    n = 0
    for c in session.execute(
            select(RecurringCommitment).where(
                RecurringCommitment.household_id == household.id,
                RecurringCommitment.active.is_(True))
    ).scalars().all():
        n += rematerialize_commitment(session, c, today=today,
                                      horizon_days=horizon_days)
    for r in session.execute(
            select(PayrollRun).where(
                PayrollRun.employment.has(household_id=household.id))
    ).scalars().all():
        n += rematerialize_payroll_run(session, r, today=today)
    for e in session.execute(
            select(Employment).where(
                Employment.household_id == household.id,
                Employment.is_active.is_(True))
    ).scalars().all():
        n += rematerialize_employment(session, e, today=today,
                                      horizon_days=horizon_days)
    for rule in session.execute(
            select(TransferRule).where(
                TransferRule.household_id == household.id,
                TransferRule.active.is_(True))
    ).scalars().all():
        n += rematerialize_transfer_rule(session, rule, today=today,
                                         horizon_days=horizon_days)
    for g in session.execute(
            select(SavingsGoal).where(
                SavingsGoal.household_id == household.id,
                SavingsGoal.active.is_(True))
    ).scalars().all():
        n += rematerialize_goal(session, g, today=today)
    for s in session.execute(
            select(Subscription).where(
                Subscription.household_id == household.id,
                Subscription.status == SubscriptionStatus.active)
    ).scalars().all():
        n += rematerialize_subscription(session, s, today=today)
    return n


def add_manual(session: Session, household_id: int, *,
               account_id: int | None, expected_date: date,
               amount_pence: int, label: str) -> ExpectedMovement:
    """A user-entered plan — its own source."""
    row = ExpectedMovement(
        household_id=household_id, account_id=account_id,
        expected_date=expected_date, amount_pence=amount_pence,
        label=label, confidence=Confidence.scheduled,
        source_type=ExpectedSource.manual, source_id=0)
    session.add(row)
    session.flush()
    row.source_id = row.id  # self-referential traceability
    session.flush()
    return row


def stream(session: Session, household_id: int, *,
           start: date, end: date,
           account_id: int | None = None) -> list[ExpectedMovement]:
    stmt = select(ExpectedMovement).where(
        ExpectedMovement.household_id == household_id,
        ExpectedMovement.expected_date >= start,
        ExpectedMovement.expected_date <= end)
    if account_id is not None:
        stmt = stmt.where(ExpectedMovement.account_id == account_id)
    return list(session.execute(
        stmt.order_by(ExpectedMovement.expected_date,
                      ExpectedMovement.id)).scalars().all())
