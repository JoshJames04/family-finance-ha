"""auth_session table + user_profile.password_hash (issue #29):
standalone-mode sessions and credential storage.

Revision ID: 0043_auth_session
Revises: 0042_anomaly
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_auth_session"
down_revision: str | None = "0042_anomaly"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id"), nullable=False),
        sa.Column("token", sa.String(length=96), nullable=False),
        sa.Column("role_at_issue", sa.String(length=20),
                  nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("token", name="uq_auth_session_token"),
    )
    op.create_index("ix_auth_session_profile_id", "auth_session",
                    ["profile_id"])
    with op.batch_alter_table("user_profile") as batch:
        batch.add_column(sa.Column(
            "password_hash", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_profile") as batch:
        batch.drop_column("password_hash")
    op.drop_table("auth_session")
