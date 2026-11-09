"""Provide database defaults for piecework timestamps.

Revision ID: 0061_delivery_timestamps
Revises: 0060_deduction_mode
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_delivery_timestamps"
down_revision: str | None = "0060_deduction_mode"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    for table in ("delivery_entry", "delivery_charge"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "created_at", existing_type=sa.DateTime(),
                server_default=sa.text("CURRENT_TIMESTAMP"))
            batch.alter_column(
                "updated_at", existing_type=sa.DateTime(),
                server_default=sa.text("CURRENT_TIMESTAMP"))


def downgrade() -> None:
    for table in ("delivery_entry", "delivery_charge"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "created_at", existing_type=sa.DateTime(), server_default=None)
            batch.alter_column(
                "updated_at", existing_type=sa.DateTime(), server_default=None)
