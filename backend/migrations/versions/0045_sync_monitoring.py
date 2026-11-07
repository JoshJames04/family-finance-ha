"""sync_cursor monitoring columns (issue #70): last_success_at,
retry_at backoff window, refresh_requested queue flag.

Revision ID: 0045_sync_monitoring
Revises: 0044_notify_deliver
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_sync_monitoring"
down_revision: str | None = "0044_notify_deliver"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sync_cursor") as batch:
        batch.add_column(sa.Column(
            "last_success_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column(
            "retry_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column(
            "refresh_requested", sa.Boolean(), nullable=False,
            server_default=sa.false()))
        batch.alter_column("refresh_requested", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("sync_cursor") as batch:
        batch.drop_column("refresh_requested")
        batch.drop_column("retry_at")
        batch.drop_column("last_success_at")
