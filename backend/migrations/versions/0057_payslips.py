"""Private payslip artifacts and extraction review state.

Revision ID: 0057_payslips
Revises: 0056_piecework_import_links
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057_payslips"
down_revision: str | None = "0056_piecework_import_links"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payslip",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("payroll_run_id", sa.Integer(),
                  sa.ForeignKey("payroll_run.id"), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("private_path", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(80), nullable=False),
        sa.Column("status", sa.Enum(
            "uploaded", "extracted", "needs_review", "confirmed", "failed",
            name="payslipstatus"), nullable=False),
        sa.Column("extracted_json", sa.Text(), nullable=True),
        sa.Column("warnings_json", sa.Text(),
                  server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("payroll_run_id", "sha256",
                            name="uq_payslip_run_sha"),
    )
    op.create_index("ix_payslip_household_id", "payslip", ["household_id"])
    op.create_index("ix_payslip_payroll_run_id", "payslip", ["payroll_run_id"])


def downgrade() -> None:
    op.drop_index("ix_payslip_payroll_run_id", table_name="payslip")
    op.drop_index("ix_payslip_household_id", table_name="payslip")
    op.drop_table("payslip")
    sa.Enum(name="payslipstatus").drop(op.get_bind(), checkfirst=True)
