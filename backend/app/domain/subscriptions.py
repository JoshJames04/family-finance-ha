"""Subscription register (issue #107).

Renewal dates advance per frequency (shared ``recurring.advance``); an
observed payment at/over the renewal rolls it forward. Cancelled
subscriptions keep their history but emit no forecast rows.
"""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.recurring import advance
from app.persistence.models import (
    Frequency,
    Subscription,
    SubscriptionPrice,
    SubscriptionStatus,
)


class SubscriptionError(Exception):
    """Domain validation failure → API maps to 409."""


_NORMALISE_RE = re.compile(r"[^a-z0-9]+")


def normalise_payee(payee: str) -> str:
    """Lowercase alphanumeric key for matching (shared with #106's
    detection pass so a detected payee hits the same register row)."""
    return _NORMALISE_RE.sub("", payee.lower())


def create(
    session: Session, household_id: int, *, payee: str,
    frequency: Frequency, expected_pence: int,
    next_renewal_date: date, category_id: int | None = None,
    account_id: int | None = None, tolerance_pct: int = 10,
    source: str = "manual", notes: str | None = None,
) -> Subscription:
    norm = normalise_payee(payee)
    if not norm:
        raise SubscriptionError("payee has no usable name characters")
    sub = Subscription(
        household_id=household_id, payee=payee,
        payee_normalised=norm, frequency=frequency,
        expected_pence=expected_pence,
        next_renewal_date=next_renewal_date,
        anchor_day=next_renewal_date.day,
        category_id=category_id, account_id=account_id,
        tolerance_pct=tolerance_pct, source=source, notes=notes)
    session.add(sub)
    session.flush()
    return sub


def _advance_once(sub: Subscription) -> None:
    sub.next_renewal_date = advance(
        sub.next_renewal_date, sub.frequency, sub.anchor_day)


def advance_renewal(sub: Subscription, as_of: date) -> None:
    """Roll the renewal forward until it is strictly after ``as_of`` —
    a payment on the renewal date counts as THIS period's."""
    while sub.next_renewal_date <= as_of:
        _advance_once(sub)


def renewal_overdue(sub: Subscription, today: date) -> bool:
    """Past the expected renewal with no observed payment — flagged
    for confirmation, not silently advanced."""
    return (sub.status is SubscriptionStatus.active
            and sub.next_renewal_date < today)


def record_price(
    session: Session, sub: Subscription, d: date,
    amount_pence: int, transaction_id: int | None = None,
) -> SubscriptionPrice:
    """Record an observed charge: price history + renewal advance."""
    if sub.status is SubscriptionStatus.cancelled:
        raise SubscriptionError("cannot record payments on a "
                                "cancelled subscription")
    p = SubscriptionPrice(
        subscription_id=sub.id, date=d, amount_pence=amount_pence,
        transaction_id=transaction_id)
    session.add(p)
    if d >= sub.next_renewal_date:
        advance_renewal(sub, d)
    session.flush()
    return p


def cancel(sub: Subscription, d: date) -> None:
    if sub.status is SubscriptionStatus.cancelled:
        raise SubscriptionError("subscription already cancelled")
    sub.status = SubscriptionStatus.cancelled
    sub.cancelled_date = d


def active_subscriptions(
    session: Session, household_id: int,
) -> list[Subscription]:
    return list(session.execute(
        select(Subscription).where(
            Subscription.household_id == household_id,
            Subscription.status == SubscriptionStatus.active)
    ).scalars().all())


def all_subscriptions(
    session: Session, household_id: int,
) -> list[Subscription]:
    return list(session.execute(
        select(Subscription)
        .where(Subscription.household_id == household_id)
        .order_by(Subscription.id)
    ).scalars().all())
