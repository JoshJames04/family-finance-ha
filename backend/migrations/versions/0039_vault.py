"""vault_entry table (issue #30): encrypted credential material,
addressed by opaque ref, never serialised.

Revision ID: 0039_vault
Revises: 0038_reconciliation
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_vault"
down_revision: str | None = "0038_reconciliation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vault_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("ref", sa.String(length=128), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False,
                  server_default="1"),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("household_id", "ref",
                            name="uq_vault_household_ref"),
    )
    op.create_index("ix_vault_entry_household_id", "vault_entry",
                    ["household_id"])


def downgrade() -> None:
    op.drop_table("vault_entry")
