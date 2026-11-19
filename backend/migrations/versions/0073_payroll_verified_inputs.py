"""Separate verified payslip bases from forecast calculation inputs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073_payroll_verified_inputs"
down_revision: str | None = "0070_transfer_confirmation_history"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "employment",
        sa.Column("salary_policy", sa.String(20), nullable=False, server_default="annual_daily"),
    )
    op.add_column("payroll_run", sa.Column("statutory_pay_date", sa.Date(), nullable=True))
    op.add_column("payroll_run", sa.Column("calculation_inputs", sa.Text(), nullable=True))
    op.add_column("payroll_run", sa.Column("signed_components", sa.Boolean(), nullable=True))
    op.add_column(
        "payroll_run", sa.Column("forecast_stale", sa.Boolean(), nullable=False, server_default="0")
    )
    for name in (
        "verified_gross_pence",
        "verified_net_pence",
        "verified_taxable_pence",
        "verified_niable_pence",
        "verified_ytd_taxable_pence",
        "verified_ytd_tax_pence",
    ):
        op.add_column("payroll_run", sa.Column(name, sa.Integer(), nullable=True))

    op.execute("UPDATE payroll_run SET forecast_stale = 1 WHERE status = 'forecast'")


def downgrade() -> None:
    # Native DROP COLUMN preserves pre-existing SQLite triggers. Batch table
    # reconstruction would silently remove the duplicate-payroll guards.
    for name in (
        "verified_ytd_tax_pence",
        "verified_ytd_taxable_pence",
        "verified_niable_pence",
        "verified_taxable_pence",
        "forecast_stale",
        "verified_gross_pence",
        "verified_net_pence",
        "signed_components",
        "calculation_inputs",
        "statutory_pay_date",
    ):
        op.drop_column("payroll_run", name)
    op.drop_column("employment", "salary_policy")
