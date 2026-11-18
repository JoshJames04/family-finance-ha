"""Effective shareholdings and employer/month payroll bonus snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0074_employer_bonuses"
down_revision = "0073_payroll_verified_inputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employment",
        sa.Column(
            "share_payment_method", sa.String(24), nullable=False, server_default="payroll_cash"
        ),
    )
    op.add_column(
        "salary_revision",
        sa.Column("shares_owned", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    for name in ("planned_bonus_pence", "noncash_bonus_pence"):
        op.add_column(
            "payroll_run", sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )
    op.create_table(
        "employment_bonus",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employment_id", sa.Integer(), sa.ForeignKey("employment.id"), nullable=False),
        sa.Column("pay_month", sa.Date(), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("salary_percentage", sa.Numeric(10, 4), nullable=True),
        sa.Column("share_value_pence", sa.Integer(), nullable=True),
        sa.Column("manual_pence", sa.Integer(), nullable=False),
        sa.Column("salary_basis_pence", sa.Integer(), nullable=False),
        sa.Column("shares_owned", sa.Numeric(18, 4), nullable=False),
        sa.Column("salary_bonus_pence", sa.Integer(), nullable=False),
        sa.Column("share_bonus_pence", sa.Integer(), nullable=False),
        sa.Column("gross_pence", sa.Integer(), nullable=False),
        sa.Column("noncash_pence", sa.Integer(), nullable=False),
        sa.Column("share_payment_method", sa.String(24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_employment_bonus_employment_id", "employment_bonus", ["employment_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE payrollcomponentkind ADD VALUE IF NOT EXISTS 'noncash'")


def downgrade() -> None:
    checks = (
        "SELECT COUNT(*) FROM employment_bonus",
        "SELECT COUNT(*) FROM salary_revision WHERE shares_owned <> 0",
        "SELECT COUNT(*) FROM payroll_run "
        "WHERE planned_bonus_pence <> 0 OR noncash_bonus_pence <> 0",
        "SELECT COUNT(*) FROM employment WHERE share_payment_method <> 'payroll_cash'",
    )
    if any(op.get_bind().execute(sa.text(sql)).scalar() for sql in checks):
        raise RuntimeError("bonus/share data exists; restore a verified pre-upgrade backup instead")
    op.drop_table("employment_bonus")
    # Native DROP COLUMN preserves the financial-integrity triggers from0068.
    # SQLite table recreation would silently remove those triggers.
    op.drop_column("payroll_run", "noncash_bonus_pence")
    op.drop_column("payroll_run", "planned_bonus_pence")
    op.drop_column("salary_revision", "shares_owned")
    op.drop_column("employment", "share_payment_method")
