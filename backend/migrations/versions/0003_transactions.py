"""transaction table — signed-pence ledger rows (issue #36).

Revision ID: 0003_transactions
Revises: 0002_core_schema
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_transactions"
down_revision: str | None = "0002_core_schema"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transaction",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("settled_date", sa.Date(), nullable=True),
        sa.Column("amount_pence", sa.Integer(), nullable=False),
        sa.Column("payee", sa.String(length=200), nullable=False),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("category.id"), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "cleared", "reconciled", "void",
                    name="transactionstatus"),
            nullable=False,
        ),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column(
            "source",
            sa.Enum("manual", "starling", "excel_import", "system",
                    name="transactionsource"),
            nullable=False,
        ),
        sa.Column("external_ref", sa.String(length=128), nullable=True),
        sa.Column("import_record_id", sa.Integer(), nullable=True),
        sa.Column("transfer_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("account_id", "source", "external_ref",
                            name="uq_transaction_dedup"),
    )
    op.create_index("ix_transaction_account_id", "transaction",
                    ["account_id"])
    op.create_index("ix_transaction_category_id", "transaction",
                    ["category_id"])
    op.create_index("ix_transaction_transfer_id", "transaction",
                    ["transfer_id"])
    op.create_index("ix_transaction_account_date", "transaction",
                    ["account_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_transaction_account_date", "transaction")
    op.drop_index("ix_transaction_transfer_id", "transaction")
    op.drop_index("ix_transaction_category_id", "transaction")
    op.drop_index("ix_transaction_account_id", "transaction")
    op.drop_table("transaction")
