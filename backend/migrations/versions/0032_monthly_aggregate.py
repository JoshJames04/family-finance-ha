"""monthly_aggregate table (issue #111).

Materialised per-month analytics — income, expense, savings, net
worth — rebuildable identically from raw transactions + loans.

Revision ID: 0032_monthly_aggregate
Revises: 0031_price_change
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_monthly_aggregate"
down_revision: str | None = "0031_price_change"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monthly_aggregate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id",
                                name="fk_monthlyagg_household"),
                  nullable=False, index=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("income_pence", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("expense_pence", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("savings_pence", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("net_worth_pence", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("household_id", "month",
                            name="uq_monthlyagg_household_month"),
    )


def downgrade() -> None:
    op.drop_table("monthly_aggregate")
