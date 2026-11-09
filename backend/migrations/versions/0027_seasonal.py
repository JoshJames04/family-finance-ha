"""seasonal_event + gift tables (issue #96).

Event type is a free-text column — a new event kind (birthdays,
weddings, …) must never need a schema change. Purchase/payment status
are DB-constrained because their transition rules are domain law.

Revision ID: 0027_seasonal
Revises: 0026_loan
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_seasonal"
down_revision: str | None = "0026_loan"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seasonal_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id",
                                name="fk_seasonal_event_household"),
                  nullable=False, index=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("budget_pence", sa.Integer(), nullable=False),
        sa.Column("savings_goal_id", sa.Integer(),
                  sa.ForeignKey("savings_goal.id",
                                name="fk_seasonal_event_goal"),
                  nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("budget_pence >= 0",
                           name="ck_seasonal_event_budget"),
        sa.CheckConstraint("year BETWEEN 1900 AND 2200",
                           name="ck_seasonal_event_year"),
        sa.UniqueConstraint("household_id", "type", "year",
                            name="uq_seasonal_event_household_type_year"),
    )
    op.create_table(
        "gift",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(),
                  sa.ForeignKey("seasonal_event.id", name="fk_gift_event"),
                  nullable=False, index=True),
        sa.Column("recipient", sa.String(120), nullable=False),
        sa.Column("idea", sa.String(300), nullable=False),
        sa.Column("planned_pence", sa.Integer(), nullable=False),
        sa.Column("actual_pence", sa.Integer(), nullable=True),
        sa.Column("purchase_status",
                  sa.Enum("idea", "planned", "purchased", "wrapped",
                          "given", name="purchasestatus"),
                  nullable=False, server_default="idea"),
        sa.Column("payment_status",
                  sa.Enum("unpaid", "paid", "refunded",
                          name="paymentstatus"),
                  nullable=False, server_default="unpaid"),
        sa.Column("transaction_id", sa.Integer(),
                  sa.ForeignKey("transaction.id", name="fk_gift_txn"),
                  nullable=True),
        sa.Column("notes", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("planned_pence >= 0",
                           name="ck_gift_planned"),
        sa.CheckConstraint("actual_pence IS NULL OR actual_pence >= 0",
                           name="ck_gift_actual"),
    )


def downgrade() -> None:
    op.drop_table("gift")
    op.drop_table("seasonal_event")
