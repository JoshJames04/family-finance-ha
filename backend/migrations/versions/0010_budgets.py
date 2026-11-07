"""budget + budget_line tables (issue #57).

Revision ID: 0010_budgets
Revises: 0009_import
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_budgets"
down_revision: str | None = "0009_import"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_period_type = sa.Enum("monthly", "annual", "custom",
                       name="budgetperiodtype")
_status = sa.Enum("draft", "approved", "locked", name="budgetstatus")
_side = sa.Enum("income", "expense", name="budgetlineside")


def upgrade() -> None:
    op.create_table(
        "budget",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False,
                  index=True),
        sa.Column("owner_profile_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id"), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("period_type", _period_type, nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", _status, nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("end_date >= start_date",
                           name="ck_budget_range"),
    )
    op.create_table(
        "budget_line",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("budget_id", sa.Integer(),
                  sa.ForeignKey("budget.id"), nullable=False, index=True),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("category.id"), nullable=False),
        sa.Column("side", _side, nullable=False),
        sa.Column("original_pence", sa.Integer(), nullable=False),
        sa.Column("revised_pence", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("budget_id", "category_id",
                            name="uq_line_budget_category"),
    )


def downgrade() -> None:
    op.drop_table("budget_line")
    op.drop_table("budget")
