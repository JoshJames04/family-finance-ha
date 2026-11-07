"""Add user_profile.dashboard_layout — per-member dashboard
arrangement (issue #123).

Revision ID: 0049_dashboard_layout
Revises: 0048_txn_corrects
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_dashboard_layout"
down_revision: str | None = "0048_txn_corrects"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_profile") as batch:
        batch.add_column(
            sa.Column("dashboard_layout", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_profile") as batch:
        batch.drop_column("dashboard_layout")
