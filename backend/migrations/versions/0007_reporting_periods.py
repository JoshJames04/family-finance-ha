"""reporting_period table (issue #40).

Revision ID: 0007_reporting_periods
Revises: 0006_refunds
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_reporting_periods"
down_revision: str | None = "0006_refunds"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reporting_period",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("household_id", "label",
                            name="uq_period_household_label"),
        sa.CheckConstraint("end_date >= start_date",
                           name="ck_period_range"),
    )
    op.create_index("ix_reporting_period_household_id",
                    "reporting_period", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_reporting_period_household_id", "reporting_period")
    op.drop_table("reporting_period")
