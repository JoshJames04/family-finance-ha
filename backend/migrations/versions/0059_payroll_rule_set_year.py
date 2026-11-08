"""Record the tax rule-set year used by each payroll run.

Revision ID: 0059_payroll_rule_set_year
Revises: 0058_payroll_variance_notification
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059_payroll_rule_set_year"
down_revision: str | None = "0058_payroll_variance_notification"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("payroll_run") as batch:
        batch.add_column(sa.Column("rule_set_year", sa.String(9),
                                  nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payroll_run") as batch:
        batch.drop_column("rule_set_year")
