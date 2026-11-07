"""anomaly_flag table (issue #114): explainable outlier flags with
a review lifecycle and feedback tuning substrate.

Revision ID: 0042_anomaly
Revises: 0041_bank_mapping
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_anomaly"
down_revision: str | None = "0041_bank_mapping"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "anomaly_flag",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id"), nullable=False),
        sa.Column("transaction_id", sa.Integer(),
                  sa.ForeignKey("transaction.id"), nullable=False),
        sa.Column("trigger",
                  sa.Enum("amount_outlier", "category_outlier",
                          "new_payee_large", "burst",
                          name="anomalytrigger"),
                  nullable=False),
        sa.Column("status",
                  sa.Enum("pending", "confirmed", "dismissed",
                          name="anomalystatus"),
                  nullable=False, server_default="pending"),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("transaction_id", "trigger",
                            name="uq_anomaly_txn_trigger"),
    )
    op.create_index("ix_anomaly_flag_household_id",
                    "anomaly_flag", ["household_id"])
    op.create_index("ix_anomaly_flag_account_id",
                    "anomaly_flag", ["account_id"])
    op.create_index("ix_anomaly_flag_transaction_id",
                    "anomaly_flag", ["transaction_id"])


def downgrade() -> None:
    op.drop_table("anomaly_flag")
