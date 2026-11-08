"""Payroll variance notification kind.

Revision ID: 0058_payroll_variance_notification
Revises: 0057_payslips
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058_payroll_variance_notification"
down_revision: str | None = "0057_payslips"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_KINDS = (
    "spend_above", "balance_below", "weekly_digest", "payday_reminder",
    "bill_reminder", "low_projected_balance", "budget_overspend",
    "unusual_transaction", "price_change", "missed_transfer", "sync_failure",
    "payroll_variance",
)


def upgrade() -> None:
    with op.batch_alter_table("notification_rule") as batch:
        batch.alter_column(
            "kind", type_=sa.Enum(*_KINDS, name="notificationkind"),
            existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("notification_rule") as batch:
        batch.alter_column(
            "kind", type_=sa.Enum(*_KINDS[:-1], name="notificationkind"),
            existing_nullable=False)
