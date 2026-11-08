"""Variable piecework charges and tips (issue #305).

Revision ID: 0054_delivery_charges
Revises: 0053_piecework
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_delivery_charges"
down_revision: str | None = "0053_piecework"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("delivery_entry") as batch:
        batch.add_column(sa.Column(
            "tips_pence", sa.Integer(), server_default="0", nullable=False))
        batch.create_check_constraint(
            "ck_delivery_hourly_rate_nonneg", "hourly_rate_pence >= 0")
        batch.create_check_constraint(
            "ck_delivery_rate_nonneg", "delivery_rate_pence >= 0")
        batch.create_check_constraint(
            "ck_delivery_tips_nonneg", "tips_pence >= 0")
    op.create_table(
        "delivery_charge",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_entry_id", sa.Integer(),
                  sa.ForeignKey("delivery_entry.id"), nullable=False),
        sa.Column("amount_pence", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount_pence >= 0",
                           name="ck_delivery_charge_nonneg"),
    )
    op.create_index("ix_delivery_charge_delivery_entry_id",
                    "delivery_charge", ["delivery_entry_id"])


def downgrade() -> None:
    op.drop_index("ix_delivery_charge_delivery_entry_id",
                  table_name="delivery_charge")
    op.drop_table("delivery_charge")
    with op.batch_alter_table("delivery_entry") as batch:
        batch.drop_constraint("ck_delivery_tips_nonneg", type_="check")
        batch.drop_constraint("ck_delivery_rate_nonneg", type_="check")
        batch.drop_constraint("ck_delivery_hourly_rate_nonneg", type_="check")
        batch.drop_column("tips_pence")
