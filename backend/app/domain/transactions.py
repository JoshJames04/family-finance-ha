"""Transaction lifecycle rules.

pending → cleared → reconciled is the happy path; void is terminal and
reconciled is immutable except via a superseding correction row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.persistence.models import (
    Transaction,
    TransactionSource,
    TransactionStatus,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

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


class CorrectionError(ValueError):
    pass


def can_transition(frm: TransactionStatus, to: TransactionStatus) -> bool:
    return to in _ALLOWED[frm]


def assert_transition(frm: TransactionStatus, to: TransactionStatus) -> None:
    if not can_transition(frm, to):
        raise InvalidTransitionError(frm, to)


def correct(session: Session, original: Transaction, reason: str,
            *, payee: str | None = None, category_id: int | None = None,
            notes: str | None = None) -> Transaction:
    """Supersede a reconciled transaction (issue #124).

    History is never edited: the original is voided and a replacement
    pending row is created carrying the corrected fields, linking back
    via `corrects_id`. The replacement re-enters the normal review
    flow — it is not reconciled by the correction itself.
    """
    if original.status is not TransactionStatus.reconciled:
        raise CorrectionError(
            "only a reconciled transaction needs a correction — "
            "editable statuses can be patched directly")
    original.status = TransactionStatus.void
    corrected = Transaction(
        account_id=original.account_id,
        date=original.date,
        settled_date=original.settled_date,
        amount_pence=original.amount_pence,
        payee=payee if payee is not None else original.payee,
        category_id=(
            category_id if category_id is not None
            else original.category_id),
        status=TransactionStatus.pending,
        notes=notes if notes is not None else (
            f"Correction of #{original.id}: {reason}"),
        source=TransactionSource.manual,
        external_ref=None,
        transfer_id=original.transfer_id,
        corrects_id=original.id)
    session.add(corrected)
    session.flush()
    return corrected
