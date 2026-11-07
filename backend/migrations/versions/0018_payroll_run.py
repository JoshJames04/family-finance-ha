"""payroll_run + payroll_component tables (issue #82).

Revision ID: 0018_payroll_run
Revises: 0017_overtime
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_payroll_run"
down_revision: str | None = "0017_overtime"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_STATUS = ("forecast", "reconciled", "corrected")
_KINDS = ("base_salary", "overtime", "bonus", "allowance",
          "unpaid_leave", "pension", "tax", "ni", "student_loan")


def upgrade() -> None:
    op.create_table(
        "payroll_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employment_id", sa.Integer(),
                  sa.ForeignKey("employment.id",
                                name="fk_payrun_employment"),
                  nullable=False, index=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Enum(*_STATUS, name="payrollrunstatus"),
                  nullable=False, server_default="forecast"),
        sa.Column("tax_year", sa.String(9), nullable=False),
        sa.Column("rule_set_version", sa.Integer(), nullable=False),
        sa.Column("gross_pence", sa.Integer(), nullable=False),
        sa.Column("net_pence", sa.Integer(), nullable=False),
        sa.Column("corrects_id", sa.Integer(),
                  sa.ForeignKey("payroll_run.id",
                                name="fk_payrun_corrects"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("period_end >= period_start",
                           name="ck_payrun_period"),
        sa.UniqueConstraint("employment_id", "period_start",
                            "period_end", "corrects_id",
                            name="uq_payrun_emp_period"),
    )
    op.create_table(
        "payroll_component",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(),
                  sa.ForeignKey("payroll_run.id",
                                name="fk_paycomp_run"),
                  nullable=False, index=True),
        sa.Column("kind", sa.Enum(*_KINDS, name="payrollcomponentkind"),
                  nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("forecast_pence", sa.Integer(), nullable=False),
        sa.Column("actual_pence", sa.Integer(), nullable=True),
        sa.UniqueConstraint("run_id", "kind", "label",
                            name="uq_paycomp_run_kind_label"),
    )


def downgrade() -> None:
    op.drop_table("payroll_component")
    op.drop_table("payroll_run")
