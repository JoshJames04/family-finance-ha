"""Provide the transaction_split table.

Revision ID: 0062_transaction_splits
Revises: 0061_delivery_timestamps
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_transaction_splits"
down_revision: str | None = "0061_delivery_timestamps"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transaction_split",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.Integer(),
                  sa.ForeignKey("transaction.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("category.id"), nullable=False),
        sa.Column("amount_pence", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Index("ix_transaction_split_transaction_id",
                 "transaction_id"),
        sa.Index("ix_transaction_split_category_id", "category_id"),
    )


def downgrade() -> None:
    op.drop_table("transaction_split")
