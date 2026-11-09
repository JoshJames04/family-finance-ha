"""budget_line.rollover (issue #60).

Revision ID: 0013_rollover
Revises: 0012_budget_templates
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_rollover"
down_revision: str | None = "0012_budget_templates"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("budget_line") as batch:
        batch.add_column(sa.Column(
            "rollover", sa.Boolean(), nullable=False,
            server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("budget_line") as batch:
        batch.drop_column("rollover")
