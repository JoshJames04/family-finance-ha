"""transfer_rule table (issue #86).

Revision ID: 0021_transfer_rule
Revises: 0020_expected_movement
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_transfer_rule"
down_revision: str | None = "0020_expected_movement"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transfer_rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id",
                                name="fk_transfer_rule_household"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source_account_id", sa.Integer(),
                  sa.ForeignKey("account.id",
                                name="fk_transfer_rule_source"),
                  nullable=False),
        sa.Column("dest_account_id", sa.Integer(),
                  sa.ForeignKey("account.id",
                                name="fk_transfer_rule_dest"),
                  nullable=False),
        sa.Column("amount_spec", sa.Text(), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("day_of_month", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("source_account_id != dest_account_id",
                           name="ck_transfer_rule_distinct_accounts"),
        sa.CheckConstraint("day_of_month BETWEEN 1 AND 31",
                           name="ck_transfer_rule_day"),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date",
                           name="ck_transfer_rule_range"),
    )


def downgrade() -> None:
    op.drop_table("transfer_rule")
