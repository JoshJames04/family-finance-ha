"""Reconciliation engine (issue #69): match actual transactions to
expected movements and close the forecast loop.

Scoring is deterministic: amount fit (0-50) + date proximity (0-30)
+ label fit (0-20). A top score ≥ AUTO_SCORE with a ≥ GAP margin
over the runner-up auto-matches; a top score ≥ REVIEW_SCORE without
that confidence queues a pending reconciliation with the ranked
candidates — never silently resolving ambiguity. Matched
transactions transition to ``reconciled`` status; the expected side
is stored as a snapshot because expected rows are re-materialised.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import categorise, contributions, settle
from app.domain.expected_matches import SALARY_SOURCES, eligible_matches
from app.persistence.models import (
    Account,
    ExpectedMovement,
    ExpectedSource,
    Reconciliation,
    ReconStatus,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

AUTO_SCORE = 75        # auto-match floor
GAP = 15               # required margin over the runner-up
REVIEW_SCORE = 40      # minimum to enter the review queue
DATE_WINDOW = 14       # candidate search radius (days)


class ReconError(ValueError):
    pass


@dataclass(frozen=True)
class ReconStats:
    scanned: int
    matched: int
    queued: int


def _amount_pts(diff: int, base: int) -> int:
    if diff == 0:
        return 50
    if base <= 0:
        return 0
    pct = diff * 100 // base
    if pct <= 1:
        return 40
    if pct <= 5:
        return 25
    if pct <= 10:
        return 10
    return 0


def _date_pts(days: int) -> int:
    if days == 0:
        return 30
    if days <= 1:
        return 25
    if days <= 3:
        return 15
    if days <= 7:
        return 5
    return 0


def _label_pts(payee: str | None, label: str) -> int:
    if not payee:
        return 0
    p, lab = payee.casefold(), label.casefold()
    if p in lab or lab in p:
        return 20
    shared = set(p.split()) & set(lab.split())
    return 10 if shared else 0


def _score(txn: Transaction, exp: ExpectedMovement) -> int:
    return (_amount_pts(
        abs(txn.amount_pence - exp.amount_pence),
        abs(exp.amount_pence))
        + _date_pts(abs((txn.date - exp.expected_date).days))
        + _label_pts(txn.payee, exp.label))


def _snapshot(r: Reconciliation, exp: ExpectedMovement,
              txn: Transaction) -> None:
    r.expected_source_type = exp.source_type.value
    r.expected_source_id = exp.source_id
    r.expected_label = exp.label
    r.expected_date = exp.expected_date
    r.expected_amount_pence = exp.amount_pence
    r.amount_drift_pence = txn.amount_pence - exp.amount_pence
    r.date_drift_days = (txn.date - exp.expected_date).days


def reconcile_account(session: Session, account: Account,
                      today: date) -> ReconStats:
    """Scan unreconciled transactions on ``account`` and match each
    against the expected movements within ±DATE_WINDOW days."""
    matched_keys, dismissed_keys, dismissed_txns = set(), set(), set()
    for r in session.scalars(select(Reconciliation).where(
            Reconciliation.account_id == account.id,
            Reconciliation.status.in_(
                [ReconStatus.matched, ReconStatus.dismissed]))):
        key = (r.expected_source_type, r.expected_source_id,
               r.expected_date)
        if r.status == ReconStatus.matched:
            matched_keys.add(key)
        else:
            dismissed_keys.add(key)
            dismissed_txns.add(r.transaction_id)
    txns = session.scalars(
        select(Transaction).where(
            Transaction.account_id == account.id,
            # pending = user-posted; cleared = settled bank feed rows
            # (issue #429 — Starling items arrive cleared, not pending)
            Transaction.status.in_(
                [TransactionStatus.pending, TransactionStatus.cleared]),
            # system rows are the plan itself (rec: materialisations),
            # never payment evidence — self-matching them fakes payment
            Transaction.source != TransactionSource.system,
            Transaction.amount_pence != 0,
            Transaction.transfer_id.is_(None),
            ~select(Reconciliation.id).where(
                Reconciliation.transaction_id == Transaction.id,
                Reconciliation.status != ReconStatus.dismissed
            ).exists(),
        ).order_by(Transaction.date)).all()
    # Resolve corrected aliases once for this account's scanned payments. An
    # already allocated live payment cannot become a new proposal elsewhere.
    used: set[int] = set()
    if txns:
        evidence = eligible_matches(transaction_ids=[txn.id for txn in txns])
        used = set(session.scalars(select(evidence.c.transaction_id)))
    matched = queued = 0
    for txn in txns:
        if txn.id in used or not categorise.transaction_editable(session, txn):
            continue
        lo = date.fromordinal(txn.date.toordinal() - DATE_WINDOW)
        hi = date.fromordinal(txn.date.toordinal() + DATE_WINDOW)
        possible = list(session.scalars(
                select(ExpectedMovement).where(
                    ExpectedMovement.account_id == account.id,
                    ExpectedMovement.household_id == account.household_id,
                    ExpectedMovement.expected_date.between(lo, hi),
                    ExpectedMovement.dismissed_at.is_(None),
                    ExpectedMovement.amount_pence != 0,
                    # sign must agree — income matches income
                    (ExpectedMovement.amount_pence < 0)
                    == (txn.amount_pence < 0))))
        totals = contributions.matched_totals(session, [e.id for e in possible])
        candidates = [
            (e, _score(txn, e)) for e in possible
            if totals[e.id] < abs(e.amount_pence)
            and (e.source_type.value, e.source_id, e.expected_date)
            not in matched_keys
            and (e.source_type.value, e.source_id, e.expected_date)
            not in dismissed_keys
            and not (txn.refund_of_id is not None and e.source_type in SALARY_SOURCES)
        ]
        candidates.sort(key=lambda c: c[1], reverse=True)
        if not candidates or candidates[0][1] < REVIEW_SCORE:
            continue
        top, score = candidates[0]
        second = candidates[1][1] if len(candidates) > 1 else 0
        # A txn the user already engaged with never auto-matches —
        # new proposals queue rather than resolving behind them.
        auto = (score >= AUTO_SCORE and score - second >= GAP
                and txn.id not in dismissed_txns)
        r = Reconciliation(
            household_id=account.household_id,
            account_id=account.id, transaction_id=txn.id,
            status=ReconStatus.pending,
            score=score,
            candidates={"ranked": [
                {"label": e.label, "date": e.expected_date.isoformat(),
                 "amount_pence": e.amount_pence, "score": s}
                for e, s in candidates[1:5]]})
        _snapshot(r, top, txn)
        if auto:
            # A competing receipt may already have satisfied the expectation.
            # Do not persist a matched snapshot when settlement did nothing.
            if settle.settle_occurrence(session, top, txn, manual=False) is None:
                continue
            r.status = ReconStatus.matched
            r.resolved_at = datetime.now()
            matched_keys.add((top.source_type.value, top.source_id,
                              top.expected_date))
            used.add(txn.id)
            txn.status = TransactionStatus.reconciled
            matched += 1
        else:
            queued += 1
        session.add(r)
    session.flush()
    return ReconStats(len(txns), matched, queued)


def confirm(session: Session, r: Reconciliation) -> None:
    """User accepts a pending reconciliation."""
    if r.status != ReconStatus.pending:
        raise ReconError("reconciliation is not pending")
    txn = session.get(Transaction, r.transaction_id)
    if txn is None:
        raise ReconError("transaction missing")
    account = session.get(Account, r.account_id)
    if (account is None or account.household_id != r.household_id
            or txn.account_id != r.account_id):
        raise contributions.MatchError("reconciliation payment scope has changed")
    if not categorise.transaction_editable(session, txn):
        raise ReconError("transaction is protected")
    # settle the live movement the snapshot proposed (issue #429) —
    # it may have been rematerialised since the proposal queued, so
    # resolve by identity rather than holding a stale row
    movement = None
    if r.expected_source_type is not None:
        target = select(ExpectedMovement).where(
                ExpectedMovement.source_type ==
                ExpectedSource(r.expected_source_type),
                ExpectedMovement.source_id == r.expected_source_id,
                ExpectedMovement.expected_date == r.expected_date,
                ExpectedMovement.account_id == r.account_id)
        movement = session.scalars(target.where(
            ExpectedMovement.household_id == r.household_id,
            ExpectedMovement.dismissed_at.is_(None))).first()
        if movement is None and session.scalar(select(target.exists())):
            raise contributions.MatchError("reconciliation target is no longer available")
    evidence = eligible_matches(transaction_ids=[txn.id])
    allocated = set(session.scalars(select(evidence.c.movement_id).where(
        evidence.c.transaction_id == txn.id)))
    if allocated and (movement is None or allocated != {movement.id}):
        raise contributions.MatchError("reconciliation payment is allocated elsewhere")
    if (movement is not None and not allocated
            and settle.settle_occurrence(session, movement, txn, manual=True) is None):
        raise contributions.MatchError("reconciliation target is already satisfied")
    # Existing valid evidence for this same target is an idempotent confirmation.
    # An absent live target retains the legacy snapshot-only review contract.
    r.status = ReconStatus.matched
    r.resolved_at = datetime.now()
    txn.status = TransactionStatus.reconciled
    session.flush()


def dismiss(session: Session, r: Reconciliation) -> None:
    """User rejects the proposed match — the transaction stays
    pending and becomes eligible for re-scoring."""
    if r.status != ReconStatus.pending:
        raise ReconError("reconciliation is not pending")
    r.status = ReconStatus.dismissed
    r.resolved_at = datetime.now()
    session.flush()
