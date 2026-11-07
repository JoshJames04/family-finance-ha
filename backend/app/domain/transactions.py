"""Transaction lifecycle rules.

pending → cleared → reconciled is the happy path; void is terminal and
reconciled is immutable except via a superseding correction row.
"""

from __future__ import annotations

from app.persistence.models import TransactionStatus

_ALLOWED: dict[TransactionStatus, frozenset[TransactionStatus]] = {
    TransactionStatus.pending: frozenset(
        {TransactionStatus.cleared, TransactionStatus.void}
    ),
    TransactionStatus.cleared: frozenset(
        {TransactionStatus.reconciled, TransactionStatus.pending,
         TransactionStatus.void}
    ),
    TransactionStatus.reconciled: frozenset(),  # corrections supersede
    TransactionStatus.void: frozenset(),
}


class InvalidTransitionError(ValueError):
    def __init__(self, frm: TransactionStatus, to: TransactionStatus) -> None:
        super().__init__(f"cannot transition {frm} → {to}")
        self.frm, self.to = frm, to


def can_transition(frm: TransactionStatus, to: TransactionStatus) -> bool:
    return to in _ALLOWED[frm]


def assert_transition(frm: TransactionStatus, to: TransactionStatus) -> None:
    if not can_transition(frm, to):
        raise InvalidTransitionError(frm, to)
