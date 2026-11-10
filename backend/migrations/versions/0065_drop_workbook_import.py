"""drop workbook-import machinery

The Excel workbook migration is complete and the file retired; the
batch/record/flag provenance tables and the transaction's
``import_record_id`` backref are removed with the feature. Downgrade
recreates the schema empty — dropped rows are not recoverable.

Revision ID: 0065_drop_workbook_import
Revises: 0065_integration_events
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_drop_workbook_import"
down_revision: str | None = "0065_integration_events"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("migration_flag")
    op.drop_table("import_record")
    op.drop_table("import_batch")
    with op.batch_alter_table("transaction") as batch:
        batch.drop_column("import_record_id")


def downgrade() -> None:
    with op.batch_alter_table("transaction") as batch:
        batch.add_column(sa.Column("import_record_id", sa.Integer(),
                                   nullable=True))
    op.create_table(
        "import_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False,
                  index=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False,
                  index=True),
        sa.Column("file_mtime", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("status", sa.Enum("dry_run", "committed", "rolled_back",
                                   name="importstatus"),
                  nullable=False),
        sa.Column("rows_read", sa.Integer(), nullable=False),
        sa.Column("rows_imported", sa.Integer(), nullable=False),
        sa.Column("rows_flagged", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True))
    op.create_table(
        "import_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(),
                  sa.ForeignKey("import_batch.id"), nullable=False,
                  index=True),
        sa.Column("sheet", sa.String(length=120), nullable=False),
        sa.Column("cell_range", sa.String(length=40), nullable=False),
        sa.Column("raw_value", sa.String(length=2000), nullable=True),
        sa.Column("interpreted_value", sa.String(length=2000),
                  nullable=True),
        sa.Column("classification",
                  sa.Enum("actual", "forecast_overwritten", "aggregate",
                          "unknown", name="rowclass"),
                  nullable=False, index=True),
        sa.Column("target_entity", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("review_flag", sa.Boolean(), nullable=False))
    op.create_table(
        "migration_flag",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(),
                  sa.ForeignKey("import_batch.id"), nullable=False,
                  index=True),
        sa.Column("defect_id", sa.String(length=4), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.String(length=2000), nullable=True),
        sa.Column("reviewer_note", sa.String(length=2000),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()))
