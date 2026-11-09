"""loans: optional owning member (mine/shared filtering, #278)

Revision ID: 0063_loan_owner
Revises: 0062_transaction_splits
Create Date: 2026-11-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_loan_owner"
down_revision: str | None = "0062_transaction_splits"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("loan") as batch:
        batch.add_column(
            sa.Column("owner_profile_id", sa.Integer(), nullable=True))
        batch.create_index("ix_loan_owner_profile_id",
                           ["owner_profile_id"])
        batch.create_foreign_key(
            "fk_loan_owner_profile", "user_profile",
            ["owner_profile_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("loan") as batch:
        batch.drop_constraint("fk_loan_owner_profile", type_="foreignkey")
        batch.drop_index("ix_loan_owner_profile_id")
        batch.drop_column("owner_profile_id")
