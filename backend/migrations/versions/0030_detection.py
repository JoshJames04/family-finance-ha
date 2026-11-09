"""recurring-payment detection candidates (issue #106).

Detected patterns land here first — nothing becomes a subscription
without a user confirm; dismissed candidates suppress identical
re-proposals on later scans.

Revision ID: 0030_detection
Revises: 0029_subscription
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_detection"
down_revision: str | None = "0029_subscription"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "detection_candidate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id",
                                name="fk_candidate_household"),
                  nullable=False, index=True),
        sa.Column("payee", sa.String(200), nullable=False),
        sa.Column("payee_normalised", sa.String(200), nullable=False,
                  index=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id",
                                name="fk_candidate_account"),
                  nullable=True),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("category.id",
                                name="fk_candidate_category"),
                  nullable=True),
        sa.Column("frequency",
                  sa.Enum("weekly", "fortnightly", "monthly",
                          "quarterly", "annually", name="frequency"),
                  nullable=False),
        sa.Column("expected_pence", sa.Integer(), nullable=False),
        sa.Column("tolerance_pct", sa.Integer(), nullable=False,
                  server_default=sa.text("10")),
        sa.Column("candidate_type",
                  sa.Enum("subscription", "variable_dd",
                          name="candidatetype"),
                  nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.Date(), nullable=False),
        sa.Column("last_seen", sa.Date(), nullable=False),
        sa.Column("status",
                  sa.Enum("pending", "confirmed", "dismissed",
                          name="candidatestatus"),
                  nullable=False, server_default="pending"),
        sa.Column("subscription_id", sa.Integer(),
                  sa.ForeignKey("subscription.id",
                                name="fk_candidate_subscription"),
                  nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("expected_pence >= 0",
                           name="ck_candidate_expected"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 100",
                           name="ck_candidate_confidence"),
        sa.CheckConstraint("occurrence_count >= 1",
                           name="ck_candidate_occurrences"),
        sa.CheckConstraint(
            "status IN ('pending','confirmed','dismissed')",
            name="ck_candidate_status"),
    )


def downgrade() -> None:
    op.drop_table("detection_candidate")
