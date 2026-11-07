"""HA notify transport columns (issue #119): rule.service +
rule.privacy_safe; delivery status/error/sent_at lifecycle.

Revision ID: 0044_notify_deliver
Revises: 0043_auth_session
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_notify_deliver"
down_revision: str | None = "0043_auth_session"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notification_rule") as batch:
        batch.add_column(sa.Column(
            "service", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column(
            "privacy_safe", sa.Boolean(), nullable=False,
            server_default=sa.true()))
    with op.batch_alter_table("notification_delivery") as batch:
        # Existing rows were "sent" in the pre-transport sense
        # (dispatched with no downstream result); backfill as sent
        # then drop the default so new rows carry the app's value.
        batch.add_column(sa.Column(
            "status", sa.String(length=20), nullable=False,
            server_default="sent"))
        batch.add_column(sa.Column(
            "error", sa.Text(), nullable=True))
        batch.add_column(sa.Column(
            "sent_at", sa.DateTime(), nullable=True))
        batch.alter_column("status", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("notification_delivery") as batch:
        batch.drop_column("sent_at")
        batch.drop_column("error")
        batch.drop_column("status")
    with op.batch_alter_table("notification_rule") as batch:
        batch.drop_column("privacy_safe")
        batch.drop_column("service")
