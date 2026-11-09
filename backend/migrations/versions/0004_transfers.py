"""transfer table + transaction.transfer_id FK (issue #37).

Revision ID: 0004_transfers
Revises: 0003_transactions
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_transfers"
down_revision: str | None = "0003_transactions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transfer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("rule_ref", sa.String(length=128), nullable=True),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_transfer_household_id", "transfer",
                    ["household_id"])
    with op.batch_alter_table("transaction") as batch:
        batch.create_foreign_key(
            "fk_transaction_transfer_id", "transfer",
            ["transfer_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("transaction") as batch:
        batch.drop_constraint("fk_transaction_transfer_id",
                              type_="foreignkey")
    op.drop_index("ix_transfer_household_id", "transfer")
    op.drop_table("transfer")
