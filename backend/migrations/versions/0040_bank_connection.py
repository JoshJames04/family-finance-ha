"""bank_connection table (issue #65): provider link state machine;
token lives in the vault under vault_ref — never on this row.

Revision ID: 0040_bank_connection
Revises: 0039_vault
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_bank_connection"
down_revision: str | None = "0039_vault"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bank_connection",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("provider",
                  sa.Enum("starling", name="bankprovider"),
                  nullable=False),
        sa.Column("vault_ref", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("status",
                  sa.Enum("pending", "connected", "degraded",
                          "revoked", name="connstatus"),
                  nullable=False, server_default="pending"),
        sa.Column("scopes", sa.String(length=400), nullable=False,
                  server_default=""),
        sa.Column("last_validated_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("household_id", "provider",
                            name="uq_bankconn_household_provider"),
    )
    op.create_index("ix_bank_connection_household_id",
                    "bank_connection", ["household_id"])


def downgrade() -> None:
    op.drop_table("bank_connection")
