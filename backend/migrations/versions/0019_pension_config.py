"""employment pension config + work days/week (issue #83).

Revision ID: 0019_pension_config
Revises: 0018_payroll_run
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_pension_config"
down_revision: str | None = "0018_payroll_run"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("employment") as batch:
        batch.add_column(sa.Column(
            "pension_mode",
            sa.Enum("none", "relief_at_source", "net_pay",
                    "salary_sacrifice", name="pensionmode"),
            server_default="none", nullable=False))
        batch.add_column(sa.Column(
            "pension_employee_bps", sa.Integer(),
            server_default="0", nullable=False))
        batch.add_column(sa.Column(
            "pension_fixed_pence", sa.Integer(),
            server_default="0", nullable=False))
        batch.add_column(sa.Column(
            "work_days_per_week", sa.Numeric(3, 1),
            server_default="5", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("employment") as batch:
        batch.drop_column("work_days_per_week")
        batch.drop_column("pension_fixed_pence")
        batch.drop_column("pension_employee_bps")
        batch.drop_column("pension_mode")
