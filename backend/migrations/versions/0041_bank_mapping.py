"""bank_account_mapping table (issue #66): provider account/space
discoveries with idempotent upsert semantics.

Revision ID: 0041_bank_mapping
Revises: 0040_bank_connection
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_bank_mapping"
down_revision: str | None = "0040_bank_connection"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bank_account_mapping",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("connection_id", sa.Integer(),
                  sa.ForeignKey("bank_connection.id"),
                  nullable=False),
        sa.Column("external_uid", sa.String(length=64),
                  nullable=False),
        sa.Column("external_label", sa.String(length=200),
                  nullable=False),
        sa.Column("external_kind", sa.String(length=40),
                  nullable=False, server_default="account"),
        sa.Column("external_category_uid", sa.String(length=64),
                  nullable=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id"), nullable=True),
        sa.Column("status",
                  sa.Enum("proposed", "linked", "ignored",
                          "closed", name="mappingstatus"),
                  nullable=False, server_default="proposed"),
        sa.Column("balance_pence", sa.Integer(), nullable=True),
        sa.Column("balance_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "external_uid",
                            name="uq_mapping_conn_ext"),
    )
    op.create_index("ix_bank_account_mapping_household_id",
                    "bank_account_mapping", ["household_id"])
    op.create_index("ix_bank_account_mapping_connection_id",
                    "bank_account_mapping", ["connection_id"])


def downgrade() -> None:
    op.drop_table("bank_account_mapping")
