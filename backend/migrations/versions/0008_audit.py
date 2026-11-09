"""audit_event table (issue #42).

Revision ID: 0008_audit
Revises: 0007_reporting_periods
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_audit"
down_revision: str | None = "0007_reporting_periods"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.String(length=4000), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_audit_event_household_id", "audit_event",
                    ["household_id"])
    op.create_index("ix_audit_event_entity_type", "audit_event",
                    ["entity_type"])


def downgrade() -> None:
    op.drop_index("ix_audit_event_entity_type", "audit_event")
    op.drop_index("ix_audit_event_household_id", "audit_event")
    op.drop_table("audit_event")
