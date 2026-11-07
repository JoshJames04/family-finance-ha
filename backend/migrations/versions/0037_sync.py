"""sync_cursor, sync_run, merge_candidate tables (issue #67).

Cursor holds the resumable position per (account, source); runs are
the monitoring record; merge candidates flag fuzzy overlaps for
review instead of double-counting.

Revision ID: 0037_sync
Revises: 0036_categorisation
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_sync"
down_revision: str | None = "0036_categorisation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_cursor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id"), nullable=False),
        sa.Column("source",
                  sa.Enum("manual", "starling", "excel_import",
                          "system", "manual_verification",
                          name="transactionsource"),
                  nullable=False),
        sa.Column("last_ref", sa.String(length=128), nullable=True),
        sa.Column("last_date", sa.Date(), nullable=True),
        sa.Column("status",
                  sa.Enum("running", "completed", "failed",
                          name="syncstatus"),
                  nullable=False, server_default="completed"),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("account_id", "source",
                            name="uq_synccursor_account_source"),
    )
    op.create_index("ix_sync_cursor_account_id", "sync_cursor",
                    ["account_id"])
    op.create_table(
        "sync_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id"), nullable=False),
        sa.Column("source",
                  sa.Enum("manual", "starling", "excel_import",
                          "system", "manual_verification",
                          name="transactionsource"),
                  nullable=False),
        sa.Column("status",
                  sa.Enum("running", "completed", "failed",
                          name="syncstatus"),
                  nullable=False, server_default="running"),
        sa.Column("fetched", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("inserted", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("deduped", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("merge_candidates", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_sync_run_account_id", "sync_run",
                    ["account_id"])
    op.create_table(
        "merge_candidate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id"), nullable=False),
        sa.Column("existing_txn_id", sa.Integer(),
                  sa.ForeignKey("transaction.id"), nullable=False),
        sa.Column("incoming", sa.JSON(), nullable=False),
        sa.Column("status",
                  sa.Enum("pending", "merged", "dismissed",
                          name="mergestatus"),
                  nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_merge_candidate_household_id",
                    "merge_candidate", ["household_id"])
    op.create_index("ix_merge_candidate_account_id",
                    "merge_candidate", ["account_id"])


def downgrade() -> None:
    op.drop_table("merge_candidate")
    op.drop_table("sync_run")
    op.drop_table("sync_cursor")
