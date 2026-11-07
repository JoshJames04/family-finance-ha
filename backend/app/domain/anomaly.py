"""Unusual transaction detection (issue #114).

Robust statistics, explainable triggers — MAD (median absolute
deviation) rather than mean/stddev because household payee history
is small and heavy-tailed; a single huge bill must not inflate the
scale it's being tested against.

Triggers (each flag carries its computed evidence in ``detail``):

- ``amount_outlier``   — |amt| > payee median + K·σ(MAD), ≥2× median
- ``category_outlier`` — same test against the category's history
- ``new_payee_large``  — payee unseen in account history AND |amt|
                        ≥ p95 of the account's prior |amounts|
- ``burst``            — ≥3 same-payee txns in one day where prior
                        same-payee daily frequency never exceeded 1

Dismissed (trigger, payee) pairs with ≥2 dismissals suppress
future flags — user feedback tunes the detector (issue AC).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.reporting import external_only
from app.persistence.models import (
    Account,
    AnomalyFlag,
    AnomalyStatus,
    AnomalyTrigger,
    Transaction,
    TransactionStatus,
)

K_SIGMA = 3.5            # robust outlier threshold
MIN_SAMPLES = 4          # payee/category history floor
NEW_PAYEE_MIN = 8        # history floor before p95 is meaningful
SCAN_DAYS = 30
DISMISS_SUPPRESS = 2     # dismissals before suppressing a pair


class AnomalyError(ValueError):
    pass


@dataclass(frozen=True)
class ScanStats:
    scanned: int
    flagged: int
    suppressed: int


def _robust_sigma(values: list[int]) -> tuple[float, float]:
    m = median(values)
    mad = median(abs(v - m) for v in values)
    return float(m), 1.4826 * float(mad)


def _p95(values: list[int]) -> float:
    s = sorted(values)
    return float(s[min(len(s) - 1, int(0.95 * len(s)))])


def _history(session: Session, account_id: int, payee: str | None,
             category_id: int | None, before: date) -> list[int]:
    q = (select(Transaction.amount_pence)
         .where(Transaction.account_id == account_id,
                Transaction.date < before,
                Transaction.status != TransactionStatus.void,
                Transaction.amount_pence < 0))
    q = external_only(q)
    if payee is not None:
        q = q.where(func.lower(Transaction.payee) == payee.lower())
    if category_id is not None:
        q = q.where(Transaction.category_id == category_id)
    return [abs(a) for a in session.scalars(q)]


def _suppressed_pairs(session: Session, account_id: int) -> set[str]:
    """(trigger|payee) keys dismissed ≥ DISMISS_SUPPRESS times —
    the feedback-tuning mechanism."""
    rows = session.execute(
        select(AnomalyFlag, Transaction.payee)
        .join(Transaction,
              Transaction.id == AnomalyFlag.transaction_id)
        .where(AnomalyFlag.account_id == account_id,
               AnomalyFlag.status == AnomalyStatus.dismissed)).all()
    counts = Counter(f"{f.trigger.value}|{(p or '').lower()}"
                     for f, p in rows)
    return {k for k, n in counts.items() if n >= DISMISS_SUPPRESS}


def _existing(session: Session, account_id: int) -> set[tuple[int, str]]:
    return {(f.transaction_id, f.trigger.value)
            for f in session.scalars(
                select(AnomalyFlag).where(
                    AnomalyFlag.account_id == account_id))}


def _flag(session: Session, account: Account, txn: Transaction,
          trigger: AnomalyTrigger, detail: dict[str, object],
          existing: set[tuple[int, str]]) -> bool:
    key = (txn.id, trigger.value)
    if key in existing:
        return False
    session.add(AnomalyFlag(
        household_id=account.household_id, account_id=account.id,
        transaction_id=txn.id, trigger=trigger, detail=detail))
    existing.add(key)
    return True


def scan_account(session: Session, account: Account,
                 today: date) -> ScanStats:
    lo = date.fromordinal(today.toordinal() - SCAN_DAYS)
    suppressed = _suppressed_pairs(session, account.id)
    existing = _existing(session, account.id)
    txns = session.scalars(
        external_only(select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.date >= lo, Transaction.date <= today,
            Transaction.status != TransactionStatus.void,
            Transaction.amount_pence < 0))
        .order_by(Transaction.date)).all()
    all_abs = [abs(a) for a in session.scalars(
        external_only(select(Transaction.amount_pence).where(
            Transaction.account_id == account.id,
            Transaction.date < lo,
            Transaction.status != TransactionStatus.void,
            Transaction.amount_pence < 0)))]
    payee_hist: dict[str, list[int]] = {}
    flagged = suppressed_n = 0

    def suppresses(trigger: AnomalyTrigger, payee: str | None) -> bool:
        return (f"{trigger.value}|{(payee or '').lower()}"
                in suppressed)

    for t in txns:
        amt = abs(t.amount_pence)
        payee = (t.payee or "").lower()

        # -- amount_outlier: within-payee MAD test
        if payee and payee not in payee_hist:
            payee_hist[payee] = _history(
                session, account.id, t.payee, None, t.date)
        hist = payee_hist.get(payee, [])
        if (len(hist) >= MIN_SAMPLES
                and not suppresses(AnomalyTrigger.amount_outlier,
                                   t.payee)):
            med, sig = _robust_sigma(hist)
            if med > 0 and amt > med + K_SIGMA * sig and amt >= 2 * med:
                flagged += _flag(
                    session, account, t, AnomalyTrigger.amount_outlier,
                    {"amount_pence": amt, "payee_median": med,
                     "robust_sigma": round(sig, 2),
                     "threshold": round(med + K_SIGMA * sig, 2),
                     "samples": len(hist)}, existing)
        elif suppresses(AnomalyTrigger.amount_outlier, t.payee):
            suppressed_n += 1

        # -- category_outlier: same test within the category
        if t.category_id is not None and not suppresses(
                AnomalyTrigger.category_outlier, t.payee):
            chist = _history(session, account.id, None,
                             t.category_id, t.date)
            if len(chist) >= MIN_SAMPLES:
                med, sig = _robust_sigma(chist)
                if (med > 0 and amt > med + K_SIGMA * sig
                        and amt >= 2 * med):
                    flagged += _flag(
                        session, account, t,
                        AnomalyTrigger.category_outlier,
                        {"amount_pence": amt,
                         "category_median": med,
                         "robust_sigma": round(sig, 2),
                         "threshold": round(med + K_SIGMA * sig, 2),
                         "samples": len(chist),
                         "category_id": t.category_id}, existing)

        # -- new_payee_large: unseen payee + ≥ p95 of history
        if (payee and not hist and len(all_abs) >= NEW_PAYEE_MIN
                and not suppresses(AnomalyTrigger.new_payee_large,
                                   t.payee)):
            p95 = _p95(all_abs)
            if amt >= p95:
                flagged += _flag(
                    session, account, t,
                    AnomalyTrigger.new_payee_large,
                    {"amount_pence": amt, "payee": t.payee,
                     "history_p95": p95,
                     "history_samples": len(all_abs)}, existing)

    # -- burst: same-payee daily count spikes beyond prior max
    by_day: Counter[tuple[str, date]] = Counter()
    for t in session.scalars(external_only(select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.date >= lo, Transaction.date <= today,
            Transaction.status != TransactionStatus.void,
            Transaction.amount_pence < 0))):
        by_day[((t.payee or "").lower(), t.date)] += 1
    prior_max: dict[str, int] = {}
    for (payee, d), n in by_day.items():
        if n < 3 or not payee:
            continue
        if payee not in prior_max:
            counts = session.scalars(
                external_only(select(func.count()).where(
                    Transaction.account_id == account.id,
                    func.lower(Transaction.payee) == payee,
                    Transaction.date < lo,
                    Transaction.status
                    != TransactionStatus.void,
                    Transaction.amount_pence < 0)
                    .group_by(Transaction.date))).all()
            prior_max[payee] = max(counts, default=0)
        if prior_max[payee] <= 1:
            day_txns = [t for t in txns
                        if (t.payee or "").lower() == payee
                        and t.date == d]
            for t in day_txns:
                if not suppresses(AnomalyTrigger.burst, t.payee):
                    flagged += _flag(
                        session, account, t, AnomalyTrigger.burst,
                        {"payee": t.payee, "same_day_count": n,
                         "prior_daily_max": prior_max[payee],
                         "date": d.isoformat()}, existing)
    session.flush()
    return ScanStats(len(txns), flagged, suppressed_n)
