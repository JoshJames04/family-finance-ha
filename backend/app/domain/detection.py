"""Recurring-payment detection (issue #106).

Scans outflow history (manual, synced, imported — any source) for
same-payee clusters on a regular interval. Stable-amount clusters
become ``subscription`` candidates; regular-but-variable-amount
clusters become ``variable_dd`` candidates and can never be confirmed
as fixed subscriptions.

Dismissal is a signature match (normalised payee + frequency + amount
class): a dismissed candidate suppresses identical re-proposals, but a
materially different amount at the same payee may resurface.

Nothing here writes a subscription — only ``confirm`` does.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from statistics import median, pstdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.recurring import advance
from app.domain.subscriptions import create as create_subscription
from app.domain.subscriptions import normalise_payee
from app.persistence.models import (
    Account,
    CandidateStatus,
    CandidateType,
    DetectionCandidate,
    Frequency,
    Subscription,
    SubscriptionPrice,
    SubscriptionStatus,
    Transaction,
    TransactionStatus,
)

MIN_OCCURRENCES = 3

# Gap ranges (days) per frequency band — wide enough for bank-holiday
# and weekend drift, narrow enough to reject noise.
_BANDS: dict[Frequency, tuple[int, int]] = {
    Frequency.weekly: (5, 9),
    Frequency.fortnightly: (11, 17),
    Frequency.monthly: (25, 35),
    Frequency.quarterly: (80, 100),
    Frequency.annually: (350, 380),
}

_STABLE_CV = 0.12   # coefficient of variation ceiling for "fixed" amount
_AMOUNT_RESURFACE_PCT = 0.25  # dismissed re-proposal needs a bigger move


class DetectionError(Exception):
    """Domain validation failure → API maps to 409."""


@dataclass
class _Cluster:
    payee: str
    payee_normalised: str
    account_id: int
    txns: list[Transaction]

    @property
    def dates(self) -> list[date]:
        return [t.date for t in self.txns]

    @property
    def amounts(self) -> list[int]:
        return [abs(t.amount_pence) for t in self.txns]


def _classify_interval(gaps: list[int]) -> Frequency | None:
    """Median gap → nearest frequency band, or None if none fits."""
    med = median(gaps)
    best: tuple[float, Frequency] | None = None
    for freq, (lo, hi) in _BANDS.items():
        if lo <= med <= hi:
            centre = (lo + hi) / 2
            dist = abs(med - centre)
            if best is None or dist < best[0]:
                best = (dist, freq)
    return best[1] if best else None


def _regularity(gaps: list[int], freq: Frequency) -> float:
    """Fraction of gaps inside the frequency band (0..1)."""
    lo, hi = _BANDS[freq]
    return sum(1 for g in gaps if lo <= g <= hi) / len(gaps)


def _confidence(
    n: int, regularity: float, cv: float, is_stable: bool,
) -> int:
    """Explainable 0-100 score: evidence, rhythm, amount stability."""
    score = 30
    score += min(30, max(0, (n - MIN_OCCURRENCES + 1) * 10))
    score += 20 if regularity == 1.0 else (
        10 if regularity >= 0.8 else 0)
    if is_stable:
        score += 20 if cv <= 0.05 else 10
    return min(100, score)


def _outflow_clusters(
    session: Session, household_id: int,
) -> list[_Cluster]:
    """Same-normalised-payee outflows, per account, household-scoped."""
    rows = session.execute(
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.amount_pence < 0,
               Transaction.transfer_id.is_(None),
               Transaction.status != TransactionStatus.void)
        .order_by(Transaction.date)
    ).scalars().all()
    groups: dict[tuple[int, str], _Cluster] = {}
    for t in rows:
        norm = normalise_payee(t.payee)
        if not norm:
            continue
        key = (t.account_id, norm)
        cluster = groups.get(key)
        if cluster is None:
            cluster = _Cluster(t.payee, norm, t.account_id, [])
            groups[key] = cluster
        cluster.txns.append(t)
    return list(groups.values())


def _registered_payees(session: Session, household_id: int) -> set[str]:
    return set(session.execute(
        select(Subscription.payee_normalised).where(
            Subscription.household_id == household_id,
            Subscription.status == SubscriptionStatus.active)
    ).scalars().all())


def _same_signature(
    cand: DetectionCandidate, freq: Frequency,
    expected_pence: int, ctype: CandidateType,
) -> bool:
    """"Identical" for re-proposal suppression: same rhythm and type,
    amount within the resurface band."""
    if cand.frequency is not freq or cand.candidate_type is not ctype:
        return False
    base = max(cand.expected_pence, expected_pence, 1)
    return (abs(cand.expected_pence - expected_pence)
            <= base * _AMOUNT_RESURFACE_PCT)


def scan(
    session: Session, household_id: int, *,
    min_occurrences: int = MIN_OCCURRENCES,
) -> list[DetectionCandidate]:
    """Cluster outflows and upsert pending candidates.

    Returns the pending candidates the scan touched (new + refreshed).
    Dismissed signatures stay dismissed; registered payees are skipped.
    """
    existing = list(session.execute(
        select(DetectionCandidate).where(
            DetectionCandidate.household_id == household_id)
    ).scalars().all())
    by_payee: dict[tuple[str, int], list[DetectionCandidate]] = {}
    for c in existing:
        by_payee.setdefault(
            (c.payee_normalised, c.account_id or -1), []).append(c)

    registered = _registered_payees(session, household_id)
    touched: list[DetectionCandidate] = []

    for cluster in _outflow_clusters(session, household_id):
        if len(cluster.txns) < min_occurrences:
            continue
        if cluster.payee_normalised in registered:
            continue
        dates = cluster.dates
        gaps = [(b - a).days for a, b in pairwise(dates)]
        freq = _classify_interval(gaps)
        if freq is None:
            continue
        reg = _regularity(gaps, freq)
        if reg < 2 / 3:
            continue

        amounts = cluster.amounts
        mean = sum(amounts) / len(amounts)
        expected = round(mean)
        cv = pstdev(amounts) / mean if mean else 0.0
        is_stable = cv <= _STABLE_CV
        ctype = (CandidateType.subscription if is_stable
                 else CandidateType.variable_dd)
        conf = _confidence(len(dates), reg, cv, is_stable)

        peers = by_payee.get(
            (cluster.payee_normalised, cluster.account_id), [])
        if any(c.status is CandidateStatus.dismissed
               and _same_signature(c, freq, expected, ctype)
               for c in peers):
            continue

        pending = next(
            (c for c in peers
             if c.status is CandidateStatus.pending
             and _same_signature(c, freq, expected, ctype)), None)
        if pending is not None:
            pending.occurrence_count = len(dates)
            pending.first_seen = dates[0]
            pending.last_seen = dates[-1]
            pending.expected_pence = expected
            pending.confidence = conf
            touched.append(pending)
            continue

        cand = DetectionCandidate(
            household_id=household_id, payee=cluster.payee,
            payee_normalised=cluster.payee_normalised,
            account_id=cluster.account_id,
            frequency=freq, expected_pence=expected,
            candidate_type=ctype, confidence=conf,
            occurrence_count=len(dates),
            first_seen=dates[0], last_seen=dates[-1])
        session.add(cand)
        session.flush()
        touched.append(cand)
        by_payee.setdefault(
            (cluster.payee_normalised, cluster.account_id),
            []).append(cand)
    return touched


def _cluster_transactions(
    session: Session, cand: DetectionCandidate,
) -> list[Transaction]:
    """The transactions behind a candidate — re-derived from the same
    cluster rule as the scan (same account + normalised payee)."""
    rows = session.execute(
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == cand.household_id,
               Transaction.account_id == cand.account_id,
               Transaction.amount_pence < 0,
               Transaction.transfer_id.is_(None),
               Transaction.status != TransactionStatus.void)
        .order_by(Transaction.date)
    ).scalars().all()
    return [t for t in rows
            if normalise_payee(t.payee) == cand.payee_normalised]


def confirm(
    session: Session, cand: DetectionCandidate, *,
    category_id: int | None = None,
    account_id: int | None = None,
    tolerance_pct: int | None = None,
) -> Subscription:
    """Promote a pending subscription candidate into the register.

    Price history is seeded from the detected transactions; renewal
    starts one period after the last observed charge. ``variable_dd``
    candidates are rejected — they are informational only.
    """
    if cand.status is not CandidateStatus.pending:
        raise DetectionError("candidate is not pending")
    if cand.candidate_type is not CandidateType.subscription:
        raise DetectionError(
            "variable-amount patterns cannot be confirmed as "
            "subscriptions")
    txns = _cluster_transactions(session, cand)
    sub = create_subscription(
        session, cand.household_id, payee=cand.payee,
        frequency=cand.frequency,
        expected_pence=cand.expected_pence,
        next_renewal_date=advance(
            cand.last_seen, cand.frequency, cand.last_seen.day),
        category_id=category_id or cand.category_id,
        account_id=account_id or cand.account_id,
        tolerance_pct=tolerance_pct or cand.tolerance_pct,
        source="detected")
    sub.anchor_day = cand.last_seen.day
    for t in txns:
        session.add(SubscriptionPrice(
            subscription_id=sub.id, date=t.date,
            amount_pence=abs(t.amount_pence), transaction_id=t.id))
    cand.status = CandidateStatus.confirmed
    cand.subscription_id = sub.id
    session.flush()
    return sub


def dismiss(
    cand: DetectionCandidate, reason: str | None = None,
) -> None:
    if cand.status is not CandidateStatus.pending:
        raise DetectionError("candidate is not pending")
    cand.status = CandidateStatus.dismissed
    if reason:
        cand.notes = (f"{cand.notes} {reason}".strip()
                      if cand.notes else reason)


def candidates(
    session: Session, household_id: int, *,
    include_dismissed: bool = False,
) -> list[DetectionCandidate]:
    q = select(DetectionCandidate).where(
        DetectionCandidate.household_id == household_id)
    if not include_dismissed:
        q = q.where(
            DetectionCandidate.status != CandidateStatus.dismissed)
    return list(session.execute(
        q.order_by(DetectionCandidate.confidence.desc(),
                   DetectionCandidate.payee)
    ).scalars().all())
