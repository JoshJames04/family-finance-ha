"""Piecework employments (issue #305): employment_type discriminator
on employment + delivery_entry log for per-shift hourly base and
free-form delivery charges (the Slow Boat model).

Revision ID: 0053_piecework
Revises: 0052_employment_end_date
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_piecework"
down_revision: str | None = "0052_employment_end_date"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("employment") as batch:
        batch.add_column(sa.Column(
            "employment_type", sa.String(20),
            server_default="salaried", nullable=False))
    op.create_table(
        "delivery_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employment_id", sa.Integer(),
                  sa.ForeignKey("employment.id"), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("hours", sa.Numeric(5, 2), nullable=False),
        sa.Column("hourly_rate_pence", sa.Integer(), nullable=False),
        sa.Column("deliveries", sa.Integer(), nullable=False),
        sa.Column("delivery_rate_pence", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("deliveries >= 0",
                           name="ck_delivery_count"),
        sa.CheckConstraint("hours >= 0", name="ck_delivery_hours"),
    )
    op.create_index("ix_delivery_entry_employment_id",
                    "delivery_entry", ["employment_id"])


def downgrade() -> None:
    op.drop_index("ix_delivery_entry_employment_id",
                  table_name="delivery_entry")
    op.drop_table("delivery_entry")
    with op.batch_alter_table("employment") as batch:
        batch.drop_column("employment_type")
