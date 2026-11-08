"""Widen bank_account_mapping uniqueness to (connection_id,
external_uid, external_category_uid): a provider space shares its
parent account's external uid but owns a distinct feed category, so
spaces must coexist with the account mapping.

Revision ID: 0050_mapping_category_key
Revises: 0049_dashboard_layout
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_mapping_category_key"
down_revision: str | None = "0049_dashboard_layout"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL so the widened unique key actually guards rows without
    # a category — SQLite treats NULLs as distinct under UNIQUE.
    op.execute(
        "UPDATE bank_account_mapping SET external_category_uid = '' "
        "WHERE external_category_uid IS NULL")
    with op.batch_alter_table("bank_account_mapping") as batch:
        batch.alter_column(
            "external_category_uid",
            existing_type=sa.String(length=64),
            nullable=False, server_default="")
        batch.drop_constraint("uq_mapping_conn_ext", type_="unique")
        batch.create_unique_constraint(
            "uq_mapping_conn_ext",
            ["connection_id", "external_uid", "external_category_uid"])


def downgrade() -> None:
    with op.batch_alter_table("bank_account_mapping") as batch:
        batch.drop_constraint("uq_mapping_conn_ext", type_="unique")
        batch.create_unique_constraint(
            "uq_mapping_conn_ext", ["connection_id", "external_uid"])
        batch.alter_column(
            "external_category_uid",
            existing_type=sa.String(length=64),
            nullable=True, server_default=None)
