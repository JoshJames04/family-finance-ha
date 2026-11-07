"""balance_verification table + 'manual_verification' transaction source
(issue #95).

A verification records asserted vs derived balance; corrections are new
ledger rows (never edits) tagged `manual_verification` so the adjusting
record explains itself and stays out of manual-entry dedup paths.

Revision ID: 0028_balance_verify
Revises: 0027_seasonal
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_balance_verify"
down_revision: str | None = "0027_seasonal"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "balance_verification",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id", name="fk_balver_account"),
                  nullable=False, index=True),
        sa.Column("asserted_pence", sa.Integer(), nullable=False),
        sa.Column("derived_pence", sa.Integer(), nullable=False),
        sa.Column("variance_pence", sa.Integer(), nullable=False),
        sa.Column("verified_date", sa.Date(), nullable=False),
        sa.Column("correction_txn_id", sa.Integer(),
                  sa.ForeignKey("transaction.id",
                                name="fk_balver_correction"),
                  nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
    )
    with op.batch_alter_table("transaction") as b:
        b.drop_constraint("ck_transaction_source", type_="check")
        b.create_check_constraint(
            "ck_transaction_source",
            "source IN ('manual','starling','excel_import','system',"
            "'manual_verification')")


def downgrade() -> None:
    with op.batch_alter_table("transaction") as b:
        b.drop_constraint("ck_transaction_source", type_="check")
        b.create_check_constraint(
            "ck_transaction_source",
            "source IN ('manual','starling','excel_import','system')")
    op.drop_table("balance_verification")
