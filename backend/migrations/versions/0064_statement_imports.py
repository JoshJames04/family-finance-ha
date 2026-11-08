"""statement_import table + file_import source (#288).

Revision ID: 0064_statement_imports
Revises: 0063_loan_owner
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064_statement_imports"
down_revision: str | None = "0063_loan_owner"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_SOURCES = ("'manual','starling','excel_import','file_import',"
            "'system','manual_verification'")


def upgrade() -> None:
    op.create_table(
        "statement_import",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="preview"),
        sa.Column("rows_total", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("rows_imported", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("rows_skipped", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
        sa.Index("ix_statement_import_household_id", "household_id"),
        sa.Index("ix_statement_import_account_id", "account_id"),
    )
    with op.batch_alter_table("transaction") as b:
        b.add_column(
            sa.Column("statement_import_id", sa.Integer(),
                      nullable=True))
        b.create_index("ix_transaction_statement_import_id",
                       ["statement_import_id"])
        b.drop_constraint("ck_transaction_source", type_="check")
        b.create_check_constraint(
            "ck_transaction_source",
            f"source IN ({_SOURCES})")


def downgrade() -> None:
    with op.batch_alter_table("transaction") as b:
        b.drop_constraint("ck_transaction_source", type_="check")
        b.create_check_constraint(
            "ck_transaction_source",
            "source IN ('manual','starling','excel_import','system',"
            "'manual_verification')")
        b.drop_index("ix_transaction_statement_import_id")
        b.drop_column("statement_import_id")
    op.drop_table("statement_import")
