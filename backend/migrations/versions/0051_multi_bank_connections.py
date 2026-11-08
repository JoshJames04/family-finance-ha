"""Drop the one-connection-per-provider rule: a household can hold
multiple links to the same bank (e.g. a personal PAT and a joint-
account PAT), each with its own vault token, mappings and sync.

Revision ID: 0051_multi_bank_connections
Revises: 0050_mapping_category_key
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0051_multi_bank_connections"
down_revision: str | None = "0050_mapping_category_key"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bank_connection") as batch:
        batch.drop_constraint(
            "uq_bankconn_household_provider", type_="unique")


def downgrade() -> None:
    # Only safe when ≤1 row per (household, provider) remains.
    with op.batch_alter_table("bank_connection") as batch:
        batch.create_unique_constraint(
            "uq_bankconn_household_provider",
            ["household_id", "provider"])
