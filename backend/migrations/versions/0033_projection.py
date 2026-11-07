"""account.min_balance_pence (issue #74).

Per-account minimum-balance threshold feeding the projection warning
state. Nullable: no threshold set → no warning.

Revision ID: 0033_projection
Revises: 0031_price_change
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_projection"
down_revision: str | None = "0031_price_change"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("account") as batch:
        batch.add_column(
            sa.Column("min_balance_pence", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("account") as batch:
        batch.drop_column("min_balance_pence")
