"""expected_movement schedule stream (issue #72).

Revision ID: 0020_expected_movement
Revises: 0019_pension_config
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_expected_movement"
down_revision: str | None = "0019_pension_config"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "expected_movement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id",
                                name="fk_expected_household"),
                  nullable=False, index=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id",
                                name="fk_expected_account"),
                  nullable=True, index=True),
        sa.Column("expected_date", sa.Date(), nullable=False),
        sa.Column("amount_pence", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("confidence",
                  sa.Enum("scheduled", "estimated",
                          name="confidence"),
                  nullable=False),
        sa.Column("source_type",
                  sa.Enum("commitment", "payroll_run", "manual",
                          name="expectedsource"),
                  nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("source_type", "source_id",
                            "expected_date",
                            name="uq_expected_source_date"),
    )


def downgrade() -> None:
    op.drop_table("expected_movement")
