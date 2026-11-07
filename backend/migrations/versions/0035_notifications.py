"""notification_rule/event/delivery tables (issue #116).

Rules evaluate triggers into dedup-keyed events; dispatch batches
pending events into deliveries (single for immediate rules, one
digest row covering N for digest rules).

Revision ID: 0035_notifications
Revises: 0034_scenario
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_notifications"
down_revision: str | None = "0034_scenario"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind",
                  sa.Enum("spend_above", "balance_below",
                          name="notificationkind"),
                  nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("target_profile_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id"), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=False,
                  server_default="ha_notify"),
        sa.Column("digest", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("digest_window_minutes", sa.Integer(),
                  nullable=False, server_default="60"),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_notification_rule_household_id",
                    "notification_rule", ["household_id"])
    op.create_table(
        "notification_delivery",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("rule_id", sa.Integer(),
                  sa.ForeignKey("notification_rule.id"),
                  nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_notification_delivery_household_id",
                    "notification_delivery", ["household_id"])
    op.create_index("ix_notification_delivery_rule_id",
                    "notification_delivery", ["rule_id"])
    op.create_table(
        "notification_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rule_id", sa.Integer(),
                  sa.ForeignKey("notification_rule.id",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("dedup_key", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state",
                  sa.Enum("pending", "delivered", "acked", "snoozed",
                          name="notificationstate"),
                  nullable=False, server_default="pending"),
        sa.Column("delivery_id", sa.Integer(),
                  sa.ForeignKey("notification_delivery.id"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("acked_at", sa.DateTime(), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("rule_id", "dedup_key",
                            name="uq_notify_event_dedup"),
    )
    op.create_index("ix_notification_event_rule_id",
                    "notification_event", ["rule_id"])


def downgrade() -> None:
    op.drop_table("notification_event")
    op.drop_table("notification_delivery")
    op.drop_table("notification_rule")
