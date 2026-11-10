"""integration_event outbox table (#437 / ADR-0009).

Revision ID: 0065_integration_events
Revises: 0064_statement_imports
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_integration_events"
down_revision: str | None = "0064_statement_imports"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False,
                  index=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("entity_kind", sa.String(32), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedup_key", sa.String(128), nullable=True),
        sa.UniqueConstraint("dedup_key",
                            name="uq_integration_event_dedup"),
    )


def downgrade() -> None:
    op.drop_table("integration_event")
