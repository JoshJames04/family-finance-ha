"""savings_goal + sinking funds (issue #91); expected_movement gains a
'savings_goal' source_type (CHECK rebuild).

Revision ID: 0025_savings_goal
Revises: 0024_expected_dismissed
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_savings_goal"
down_revision: str | None = "0024_expected_dismissed"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "savings_goal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id",
                                name="fk_savings_goal_household"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("goal_type",
                  sa.Enum("emergency", "holiday", "christmas",
                          "annual_reserve", "general",
                          name="goaltype"),
                  nullable=False),
        sa.Column("target_pence", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id",
                                name="fk_savings_goal_account"),
                  nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("target_pence > 0",
                           name="ck_savings_goal_target"),
        sa.CheckConstraint(
            "target_date IS NULL OR target_date >= start_date",
            name="ck_savings_goal_dates"),
    )
    with op.batch_alter_table("expected_movement") as b:
        b.alter_column(
            "source_type",
            type_=sa.Enum("commitment", "payroll_run", "transfer_rule",
                          "savings_goal", "manual",
                          name="expectedsource"),
            existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("expected_movement") as b:
        b.alter_column(
            "source_type",
            type_=sa.Enum("commitment", "payroll_run", "transfer_rule",
                          "manual", name="expectedsource"),
            existing_nullable=False)
    op.drop_table("savings_goal")
