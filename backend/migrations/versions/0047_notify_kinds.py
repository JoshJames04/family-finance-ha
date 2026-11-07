"""Widen notification_rule.kind enum — scheduled + alert kinds
(issues #117, #118).

Revision ID: 0047_notify_kinds
Revises: 0045_sync_monitoring
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_notify_kinds"
down_revision: str | None = "0046_energy"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_KINDS = (
    "spend_above", "balance_below",
    "weekly_digest", "payday_reminder", "bill_reminder",
    "low_projected_balance", "budget_overspend",
    "unusual_transaction", "price_change", "missed_transfer",
    "sync_failure",
)


def upgrade() -> None:
    with op.batch_alter_table("notification_rule") as batch:
        batch.alter_column(
            "kind",
            type_=sa.Enum(*_KINDS, name="notificationkind"),
            existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("notification_rule") as batch:
        batch.alter_column(
            "kind",
            type_=sa.Enum("spend_above", "balance_below",
                          name="notificationkind"),
            existing_nullable=False)
