"""overtime_entry table + employment overtime multipliers (issue #79).

Revision ID: 0017_overtime
Revises: 0016_tax_rule_set
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_overtime"
down_revision: str | None = "0016_tax_rule_set"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "overtime_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employment_id", sa.Integer(),
                  sa.ForeignKey("employment.id",
                                name="fk_overtime_employment"),
                  nullable=False, index=True),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("hours", sa.Numeric(5, 2), nullable=False),
        sa.Column("multiplier_bps", sa.Integer(), nullable=False),
        sa.Column("day_type",
                  sa.Enum("weekday", "saturday", "sunday",
                          "bank_holiday", name="daytype"),
                  nullable=False),
        sa.Column("status",
                  sa.Enum("draft", "submitted", "approved", "rejected",
                          name="overtimestatus"),
                  nullable=False, server_default="draft"),
        sa.Column("hourly_rate_pence", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("hours > 0", name="ck_overtime_hours_pos"),
        sa.CheckConstraint("multiplier_bps > 0",
                           name="ck_overtime_mult_pos"),
    )
    with op.batch_alter_table("employment") as batch:
        batch.add_column(sa.Column(
            "overtime_multipliers", sa.String(400),
            server_default="{}", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("employment") as batch:
        batch.drop_column("overtime_multipliers")
    op.drop_table("overtime_entry")
