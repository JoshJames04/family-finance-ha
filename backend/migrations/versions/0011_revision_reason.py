"""budget_line.revision_reason (issue #58).

Revision ID: 0011_revision_reason
Revises: 0010_budgets
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_revision_reason"
down_revision: str | None = "0010_budgets"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("budget_line") as batch:
        batch.add_column(
            sa.Column("revision_reason", sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("budget_line") as batch:
        batch.drop_column("revision_reason")
