"""subscription register + price history (issue #107);
expected_movement gains a 'subscription' source_type (enum rebuild).

Cancelled subscriptions stay in the register (with history) but stop
emitting forecast rows — the register is the memory, not the forecast.

Revision ID: 0029_subscription
Revises: 0028_balance_verify
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_subscription"
down_revision: str | None = "0028_balance_verify"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id",
                                name="fk_subscription_household"),
                  nullable=False, index=True),
        sa.Column("payee", sa.String(200), nullable=False),
        sa.Column("payee_normalised", sa.String(200), nullable=False,
                  index=True),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("category.id",
                                name="fk_subscription_category"),
                  nullable=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id",
                                name="fk_subscription_account"),
                  nullable=True),
        sa.Column("frequency",
                  sa.Enum("weekly", "fortnightly", "monthly",
                          "quarterly", "annually", name="frequency"),
                  nullable=False),
        sa.Column("expected_pence", sa.Integer(), nullable=False),
        sa.Column("tolerance_pct", sa.Integer(), nullable=False,
                  server_default=sa.text("10")),
        sa.Column("next_renewal_date", sa.Date(), nullable=False),
        sa.Column("anchor_day", sa.Integer(), nullable=False),
        sa.Column("status",
                  sa.Enum("active", "cancelled",
                          name="subscriptionstatus"),
                  nullable=False, server_default="active"),
        sa.Column("cancelled_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False,
                  server_default="manual"),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("expected_pence >= 0",
                           name="ck_subscription_expected"),
        sa.CheckConstraint("tolerance_pct BETWEEN 0 AND 100",
                           name="ck_subscription_tolerance"),
        sa.CheckConstraint("anchor_day BETWEEN 1 AND 31",
                           name="ck_subscription_anchor"),
        sa.CheckConstraint(
            "status IN ('active','cancelled')",
            name="ck_subscription_status"),
    )
    op.create_table(
        "subscription_price",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subscription_id", sa.Integer(),
                  sa.ForeignKey("subscription.id",
                                name="fk_subprice_subscription"),
                  nullable=False, index=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount_pence", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(),
                  sa.ForeignKey("transaction.id",
                                name="fk_subprice_txn"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.CheckConstraint("amount_pence >= 0",
                           name="ck_subscription_price_amount"),
    )
    with op.batch_alter_table("expected_movement") as b:
        b.alter_column(
            "source_type",
            type_=sa.Enum("commitment", "payroll_run", "transfer_rule",
                          "savings_goal", "subscription", "manual",
                          name="expectedsource"),
            existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("expected_movement") as b:
        b.alter_column(
            "source_type",
            type_=sa.Enum("commitment", "payroll_run", "transfer_rule",
                          "savings_goal", "manual",
                          name="expectedsource"),
            existing_nullable=False)
    op.drop_table("subscription_price")
    op.drop_table("subscription")
