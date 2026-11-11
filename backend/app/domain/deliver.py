"""HA notify delivery (issue #119).

Transport for ``NotificationDelivery`` rows: compose a body from the
delivery's events, honour per-rule privacy mode (no digits, amounts
or payees on notification surfaces — detail stays in-app), call the
injected notifier, and record sent/failed. Delivery errors are
captured on the row — a broken notify service can never take the
rules engine down with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationRule,
)


class HaNotifier(Protocol):
    """HA notify service caller — injectable for tests. ``service``
    is the notify suffix (``mobile_app_pixel_7``); None means the
    household-wide ``notify.notify`` broadcast."""

    def notify(self, service: str | None, title: str,
               message: str) -> None: ...


def _fmt_pence(pence: int) -> str:
    return f"£{abs(pence) / 100:,.2f}"


def _event_line(payload: dict[str, object]) -> str:
    """Human summary for detail mode — payload keys produced by the
    rule evaluators (payee/amount or balance/threshold)."""
    if "payee" in payload:
        amt = int(str(payload.get("amount_pence", 0)))
        return f"{payload['payee']} {_fmt_pence(amt)}"
    if "balance_pence" in payload:
        bal = int(str(payload["balance_pence"]))
        thr = int(str(payload.get("threshold_pence", 0)))
        return (f"Balance {_fmt_pence(bal)} is below "
                f"{_fmt_pence(thr)}")
    return "activity recorded"


def compose(rule: NotificationRule,
            events: list[NotificationEvent]) -> tuple[str, str]:
    """(title, message). Privacy mode is a hard lock-screen
    contract: zero digits, zero payees, zero amounts — the member
    opens the app for detail."""
    if rule.privacy_safe:
        message = ("New household-finance activity — open the app "
                   "for details" if len(events) == 1 else
                   "Household-finance digest ready — open the app "
                   "for details")
        return "Family Finance", message
    lines = [_event_line(e.payload or {}) for e in events]
    if len(lines) > 5:
        lines = lines[:5] + [f"…and {len(events) - 5} more"]
    return rule.name, "\n".join(lines)


@dataclass(frozen=True)
class DeliverResult:
    sent: int
    failed: int


def deliver_pending(session: Session, household_id: int,
                    notifier: HaNotifier,
                    now: datetime | None = None) -> DeliverResult:
    """Attempt every pending delivery for the household. Per-
    delivery try/except: failures are logged on the row and
    surfaced via status, never raised."""
    now = now or datetime.now()
    deliveries = session.scalars(
        select(NotificationDelivery).where(
            NotificationDelivery.household_id == household_id,
            NotificationDelivery.status == "pending")
        .order_by(NotificationDelivery.id)).all()
    sent = failed = 0
    for d in deliveries:
        rule = session.get(NotificationRule, d.rule_id)
        if rule is None:
            d.status, d.error = "failed", "rule deleted"
            failed += 1
            session.flush()
            continue
        title, message = compose(rule, list(d.events))
        try:
            notifier.notify(rule.service, title, message)
        except Exception as exc:  # noqa: BLE001 — captured by design
            d.status, d.error = "failed", str(exc)[:500]
            failed += 1
        else:
            d.status, d.error, d.sent_at = "sent", None, now
            sent += 1
        # Commit per delivery — the notify call above is network I/O
        # and an uncommitted writer held across it stalls concurrent
        # write requests for the whole round-trip (#460).
        session.commit()
    return DeliverResult(sent=sent, failed=failed)
