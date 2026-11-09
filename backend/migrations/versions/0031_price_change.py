"""price_change_event table (issue #108).

A matching charge outside a subscription's tolerance records exactly
one event (old/new/date). The event is the alert feed; the register's
price history stays the record.

Revision ID: 0031_price_change
Revises: 0030_detection
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_price_change"
down_revision: str | None = "0030_detection"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_change_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id",
                                name="fk_pricechange_household"),
                  nullable=False, index=True),
        sa.Column("subscription_id", sa.Integer(),
                  sa.ForeignKey("subscription.id",
                                name="fk_pricechange_subscription"),
                  nullable=False, index=True),
        sa.Column("detected_date", sa.Date(), nullable=False),
        sa.Column("old_pence", sa.Integer(), nullable=False),
        sa.Column("new_pence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("subscription_id", "detected_date",
                            name="uq_pricechange_once"),
    )


def downgrade() -> None:
    op.drop_table("price_change_event")
