"""Explicit PAYE versus gross-only deduction mode.

Revision ID: 0060_deduction_mode
Revises: 0059_payroll_rule_set_year
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0060_deduction_mode"
down_revision: str | None = "0059_payroll_rule_set_year"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("employment") as batch:
        batch.add_column(sa.Column(
            "deduction_mode", sa.String(20),
            server_default="paye", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("employment") as batch:
        batch.drop_column("deduction_mode")
