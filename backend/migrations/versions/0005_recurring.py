"""recurring_commitment table (issue #38).

Revision ID: 0005_recurring
Revises: 0004_transfers
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_recurring"
down_revision: str | None = "0004_transfers"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recurring_commitment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id"), nullable=False),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("category.id"), nullable=True),
        sa.Column("amount_pence", sa.Integer(), nullable=True),
        sa.Column("formula", sa.String(length=200), nullable=True),
        sa.Column(
            "frequency",
            sa.Enum("weekly", "fortnightly", "monthly", "quarterly",
                    "annually", name="frequency"),
            nullable=False,
        ),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("next_date", sa.Date(), nullable=False),
        sa.Column("last_generated", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=True),
        sa.Column("generated_count", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.Enum("manual", "starling", "excel_import", "system",
                    name="transactionsource"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "(amount_pence IS NOT NULL) <> (formula IS NOT NULL)",
            name="ck_recurring_amount_xor_formula"),
        sa.CheckConstraint(
            "frequency IN ('weekly','fortnightly','monthly','quarterly',"
            "'annually')", name="ck_recurring_frequency"),
        sa.CheckConstraint(
            "day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31",
            name="ck_recurring_dom"),
    )
    op.create_index("ix_recurring_commitment_household_id",
                    "recurring_commitment", ["household_id"])
    op.create_index("ix_recurring_commitment_account_id",
                    "recurring_commitment", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_recurring_commitment_account_id",
                  "recurring_commitment")
    op.drop_index("ix_recurring_commitment_household_id",
                  "recurring_commitment")
    op.drop_table("recurring_commitment")
