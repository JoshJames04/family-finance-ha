"""payroll_override run-scoped manual adjustments (issue #85).

Revision ID: 0022_payroll_override
Revises: 0021_transfer_rule
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_payroll_override"
down_revision: str | None = "0021_transfer_rule"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_KINDS = ["base_salary", "overtime", "bonus", "allowance",
          "unpaid_leave", "pension", "tax", "ni", "student_loan"]


def upgrade() -> None:
    op.create_table(
        "payroll_override",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(),
                  sa.ForeignKey("payroll_run.id",
                                name="fk_payroll_override_run"),
                  nullable=False, index=True),
        sa.Column("kind", sa.Enum(*_KINDS, name="payrollcomponentkind"),
                  nullable=False),
        sa.Column("delta_pence", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("delta_pence != 0",
                           name="ck_payroll_override_nonzero"),
    )


def downgrade() -> None:
    op.drop_table("payroll_override")
