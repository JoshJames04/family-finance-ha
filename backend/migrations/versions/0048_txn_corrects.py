"""Add transaction.corrects_id — superseding correction chain (#124).

A reconciled transaction is never edited: a correction voids it and
lands a replacement row pointing back via `corrects_id`.

Revision ID: 0048_txn_corrects
Revises: 0047_notify_kinds
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_txn_corrects"
down_revision: str | None = "0047_notify_kinds"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transaction") as batch:
        batch.add_column(
            sa.Column("corrects_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_transaction_corrects", "transaction", ["corrects_id"],
            ["id"])
        batch.create_index("ix_transaction_corrects", ["corrects_id"])


def downgrade() -> None:
    with op.batch_alter_table("transaction") as batch:
        batch.drop_index("ix_transaction_corrects")
        batch.drop_constraint("fk_transaction_corrects",
                              type_="foreignkey")
        batch.drop_column("corrects_id")
