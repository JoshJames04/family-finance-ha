"""Immutable source links for historical piecework mapping.

Revision ID: 0056_piecework_import_links
Revises: 0055_employment_start_date
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056_piecework_import_links"
down_revision: str | None = "0055_employment_start_date"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("delivery_entry") as batch:
        batch.add_column(sa.Column("source_transaction_id", sa.Integer(),
                                  nullable=True))
        batch.add_column(sa.Column("imported_total_pence", sa.Integer(),
                                  nullable=True))
        batch.add_column(sa.Column("needs_review", sa.Boolean(),
                                  server_default="0", nullable=False))
        batch.create_unique_constraint(
            "uq_delivery_source_transaction", ["source_transaction_id"])
        batch.create_foreign_key(
            "fk_delivery_source_transaction", "transaction",
            ["source_transaction_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("delivery_entry") as batch:
        batch.drop_constraint("fk_delivery_source_transaction",
                              type_="foreignkey")
        batch.drop_constraint("uq_delivery_source_transaction", type_="unique")
        batch.drop_column("needs_review")
        batch.drop_column("imported_total_pence")
        batch.drop_column("source_transaction_id")
