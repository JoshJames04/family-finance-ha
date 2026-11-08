"""Employment end dates: an ended employment stops generating
payroll/forecast figures without erasing its history (#304).

Revision ID: 0052_employment_end_date
Revises: 0051_multi_bank_connections
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_employment_end_date"
down_revision: str | None = "0051_multi_bank_connections"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("employment") as batch:
        batch.add_column(sa.Column("end_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("employment") as batch:
        batch.drop_column("end_date")
