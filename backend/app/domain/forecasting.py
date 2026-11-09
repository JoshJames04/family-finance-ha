"""Issue #113 — explainable expenditure forecasting.

Every forecast row names its METHOD and INPUT WINDOW — no opaque
numbers:

- ``commitment`` (confidence ``confirmed``): sum of a
  RecurringCommitment's occurrences in the month, expanded by the same
  ``recurring.occurrences`` generator the expected-movement stream
  uses.
- ``seasonal_index`` (``assumption``): trailing mean × the month's
  seasonal multiplier — mean same-calendar-month actual / overall
  mean. Requires ≥12 history months, so the multiplier rests on a
  full seasonal cycle.
- ``trailing_average`` (``assumption`` ≥6 months, else ``estimate``):
  plain mean of monthly actuals.

A category-month can emit up to two rows: the confirmed slice plus a
modelled residual (modelled total − confirmed, floored at 0). History
includes committed spend, so the modelled total already covers it —
only the variable remainder needs modelling.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.recurring import occurrences
from app.domain.reporting import external_only
from app.persistence.models import (
    Account,
    RecurringCommitment,
    Transaction,
    TransactionStatus,
)

Method = Literal["commitment", "seasonal_index", "trailing_average"]
ConfidenceName = Literal["confirmed", "assumption", "estimate"]

SEASONAL_MIN_HISTORY = 12
ASSUMPTION_MIN_HISTORY = 6


@dataclass(frozen=True)
class ForecastRow:
    category_id: int | None
    month: date                     # month-start
    amount_pence: int               # positive = spend
    method: Method
    confidence: ConfidenceName
    input_start: date | None        # actuals window fed to the model
    input_end: date | None
    input_months: int               # months with nonzero actuals


def _month_start(y: int, m: int) -> date:
    return date(y, m, 1)


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return _month_start(d.year + m // 12, m % 12 + 1)


def _actuals(session: Session, household_id: int, start: date,
             end: date) -> dict[tuple[int | None, date], int]:
    """Per (category, month) positive spend in [start, end) —
    external-only so internal transfers never count as expenditure."""
    month_expr = func.strftime("%Y-%m", Transaction.date)
    stmt = (
        select(month_expr,
               Transaction.category_id,
               func.coalesce(func.sum(-Transaction.amount_pence), 0))
        .join(Account, Transaction.account_id == Account.id)
        .where(Account.household_id == household_id,
               Transaction.amount_pence < 0,
               Transaction.status != TransactionStatus.void,
               Transaction.date >= start,
               Transaction.date < end)
        .group_by(month_expr, Transaction.category_id))
    stmt = external_only(stmt)
    out: dict[tuple[int | None, date], int] = {}
    for ym, cat, amt in session.execute(stmt):
        out[(cat, _month_start(int(ym[:4]), int(ym[5:])))] = int(amt)
    return out


def _commitment_spend(session: Session, household_id: int,
                      horizon: date
                      ) -> dict[tuple[int, date], int]:
    """Per (category, month) committed spend across all occurrences —
    same expansion the expected-movement stream uses."""
    cats = session.scalars(
        select(RecurringCommitment).where(
            RecurringCommitment.household_id == household_id,
            RecurringCommitment.active.is_(True),
            RecurringCommitment.category_id.isnot(None),
            RecurringCommitment.amount_pence < 0))
    out: dict[tuple[int, date], int] = {}
    for c in cats:
        assert c.category_id is not None
        assert c.amount_pence is not None
        for d in occurrences(c, horizon):
            key = (c.category_id, _month_start(d.year, d.month))
            out[key] = out.get(key, 0) + (-c.amount_pence)
    return out


def _model(actuals: dict[date, int], target: date,
           history: list[date]) -> tuple[int, Method, ConfidenceName]:
    """Modelled spend for ``target`` month from per-month actuals.

    Seasonal method: ``recent_level × seasonal_index`` — the mean of
    the last 3 history months (current level) multiplied by
    ``same-calendar-month mean / overall mean`` over the window. A
    December that always runs double produces a doubled December
    forecast at the current spend level."""
    observed = [actuals.get(m, 0) for m in history]
    n = sum(1 for a in observed if a > 0)
    mean = sum(observed) // len(observed) if observed else 0
    if n >= SEASONAL_MIN_HISTORY and mean > 0:
        same_month = [actuals.get(m, 0) for m in history
                      if m.month == target.month]
        seasonal_mean = (sum(same_month) // len(same_month)
                         if same_month else mean)
        recent = observed[-3:] or observed
        level = sum(recent) // len(recent)
        return (round(level * seasonal_mean / mean),
                "seasonal_index", "assumption")
    return mean, "trailing_average", (
        "assumption" if n >= ASSUMPTION_MIN_HISTORY else "estimate")


def forecast(session: Session, household_id: int,
             from_month: date | None = None, months: int = 12,
             history_months: int = 12) -> list[ForecastRow]:
    """Per-category monthly spend forecast with method + confidence
    provenance. Deterministic in ``from_month`` for tests."""
    today = date.today()
    anchor = _month_start(
        *(from_month.year, from_month.month) if from_month
        else (today.year, today.month))
    months = max(1, min(months, 36))
    history_months = max(1, min(history_months, 60))

    hist_start = _add_months(anchor, -history_months)
    horizon_end = _add_months(anchor, months)

    actuals = _actuals(session, household_id, hist_start, anchor)
    committed = _commitment_spend(
        session, household_id, horizon_end)

    per_cat: dict[int | None, dict[date, int]] = {}
    for (cat, m), amt in actuals.items():
        per_cat.setdefault(cat, {})[m] = amt
    history = [_add_months(anchor, -k)
               for k in range(history_months, 0, -1)]
    future = [_add_months(anchor, k) for k in range(months)]

    cats = set(per_cat) | {cat for cat, _ in committed}
    rows: list[ForecastRow] = []
    for cat in sorted(cats, key=lambda c: (c is None, c)):
        cat_actuals = per_cat.get(cat, {})
        window = [cat_actuals.get(m, 0) for m in history]
        input_months = sum(1 for a in window if a > 0)
        for m in future:
            confirmed = committed.get((cat, m), 0) if cat else 0
            modelled, method, conf = _model(
                cat_actuals, m, history)
            residual = max(0, modelled - confirmed)
            win = (hist_start, _add_months(anchor, -1))
            if confirmed:
                rows.append(ForecastRow(
                    cat, m, confirmed, "commitment", "confirmed",
                    win[0], win[1], input_months))
            if residual or not confirmed:
                rows.append(ForecastRow(
                    cat, m, residual, method, conf,
                    win[0], win[1], input_months))
    rows.sort(key=lambda r: (r.month, r.category_id or 0,
                             r.method))
    return rows
