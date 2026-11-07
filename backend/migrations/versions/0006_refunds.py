"""transaction.refund_of_id self-FK (issue #39).

Revision ID: 0006_refunds
Revises: 0005_recurring
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_refunds"
down_revision: str | None = "0005_recurring"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transaction") as batch:
        batch.add_column(
            sa.Column("refund_of_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_transaction_refund_of", "transaction",
            ["refund_of_id"], ["id"])
        batch.create_index("ix_transaction_refund_of_id",
                           ["refund_of_id"])


def downgrade() -> None:
    with op.batch_alter_table("transaction") as batch:
        batch.drop_index("ix_transaction_refund_of_id")
        batch.drop_constraint("fk_transaction_refund_of",
                              type_="foreignkey")
        batch.drop_column("refund_of_id")
