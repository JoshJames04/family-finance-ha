"""create app_meta platform table

Revision ID: 0001_app_meta
Revises:
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_app_meta"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_meta",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.String(length=1024), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_meta")
