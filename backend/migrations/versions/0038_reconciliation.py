"""reconciliation table (issue #69).

Links actual transactions to the expected movements they settle.
The expected side is a snapshot (expected rows are re-materialised),
plus ranked candidates for the review queue.

Revision ID: 0038_reconciliation
Revises: 0037_sync
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_reconciliation"
down_revision: str | None = "0037_sync"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reconciliation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id"), nullable=False),
        sa.Column("transaction_id", sa.Integer(),
                  sa.ForeignKey("transaction.id"), nullable=False),
        sa.Column("status",
                  sa.Enum("pending", "matched", "dismissed",
                          name="reconstatus"),
                  nullable=False, server_default="pending"),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("expected_source_type", sa.String(length=40),
                  nullable=True),
        sa.Column("expected_source_id", sa.Integer(), nullable=True),
        sa.Column("expected_label", sa.String(length=200),
                  nullable=True),
        sa.Column("expected_date", sa.Date(), nullable=True),
        sa.Column("expected_amount_pence", sa.Integer(),
                  nullable=True),
        sa.Column("amount_drift_pence", sa.Integer(), nullable=True),
        sa.Column("date_drift_days", sa.Integer(), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False,
                  server_default="{}"),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_reconciliation_household_id",
                    "reconciliation", ["household_id"])
    op.create_index("ix_reconciliation_account_id",
                    "reconciliation", ["account_id"])
    op.create_index("ix_reconciliation_transaction_id",
                    "reconciliation", ["transaction_id"])


def downgrade() -> None:
    op.drop_table("reconciliation")
