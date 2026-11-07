"""expected_movement.dismissed_at: write-off marker (issue #88).

Revision ID: 0024_expected_dismissed
Revises: 0023_expected_match
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_expected_dismissed"
down_revision: str | None = "0023_expected_match"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "expected_movement",
        sa.Column("dismissed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("expected_movement", "dismissed_at")
