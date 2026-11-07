"""loan + repayment tables (issue #93).

Revision ID: 0026_loan
Revises: 0025_savings_goal
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_loan"
down_revision: str | None = "0025_savings_goal"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id", name="fk_loan_household"),
                  nullable=False, index=True),
        sa.Column("party", sa.String(120), nullable=False),
        sa.Column("direction",
                  sa.Enum("we_owe", "they_owe", name="loandirection"),
                  nullable=False),
        sa.Column("principal_pence", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("expected_payment_pence", sa.Integer(), nullable=True),
        sa.Column("interest_bps_pa", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("principal_pence > 0",
                           name="ck_loan_principal"),
        sa.CheckConstraint(
            "interest_bps_pa IS NULL OR interest_bps_pa >= 0",
            name="ck_loan_interest"),
    )
    op.create_table(
        "repayment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("loan_id", sa.Integer(),
                  sa.ForeignKey("loan.id", name="fk_repayment_loan"),
                  nullable=False, index=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount_pence", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(),
                  sa.ForeignKey("transaction.id",
                                name="fk_repayment_transaction"),
                  nullable=True),
        sa.Column("notes", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("amount_pence > 0",
                           name="ck_repayment_positive"),
    )


def downgrade() -> None:
    op.drop_table("repayment")
    op.drop_table("loan")
