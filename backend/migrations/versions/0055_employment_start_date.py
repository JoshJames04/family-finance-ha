"""Explicit employment starts for schedule clipping.

Revision ID: 0055_employment_start_date
Revises: 0054_delivery_charges
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_employment_start_date"
down_revision: str | None = "0054_delivery_charges"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("employment") as batch:
        batch.add_column(sa.Column("start_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("employment") as batch:
        batch.drop_column("start_date")
