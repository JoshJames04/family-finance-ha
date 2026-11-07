"""import_batch, import_record, migration_flag (issue #45).

Revision ID: 0009_import
Revises: 0008_audit
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_import"
down_revision: str | None = "0008_audit"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_mtime", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("status",
                  sa.Enum("dry_run", "committed", "rolled_back",
                          name="importstatus"),
                  nullable=False),
        sa.Column("rows_read", sa.Integer(), nullable=False),
        sa.Column("rows_imported", sa.Integer(), nullable=False),
        sa.Column("rows_flagged", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_import_batch_household_id", "import_batch",
                    ["household_id"])
    op.create_index("ix_import_batch_sha256", "import_batch", ["sha256"])

    op.create_table(
        "import_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(),
                  sa.ForeignKey("import_batch.id"), nullable=False),
        sa.Column("sheet", sa.String(length=120), nullable=False),
        sa.Column("cell_range", sa.String(length=40), nullable=False),
        sa.Column("raw_value", sa.String(length=2000), nullable=True),
        sa.Column("interpreted_value", sa.String(length=2000),
                  nullable=True),
        sa.Column("classification",
                  sa.Enum("actual", "forecast_overwritten", "aggregate",
                          "unknown", name="rowclass"),
                  nullable=False),
        sa.Column("target_entity", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("review_flag", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_import_record_batch_id", "import_record",
                    ["batch_id"])
    op.create_index("ix_import_record_classification", "import_record",
                    ["classification"])

    op.create_table(
        "migration_flag",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(),
                  sa.ForeignKey("import_batch.id"), nullable=False),
        sa.Column("defect_id", sa.String(length=4), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.String(length=2000), nullable=True),
        sa.Column("reviewer_note", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_migration_flag_batch_id", "migration_flag",
                    ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_migration_flag_batch_id", "migration_flag")
    op.drop_table("migration_flag")
    op.drop_index("ix_import_record_classification", "import_record")
    op.drop_index("ix_import_record_batch_id", "import_record")
    op.drop_table("import_record")
    op.drop_index("ix_import_batch_sha256", "import_batch")
    op.drop_index("ix_import_batch_household_id", "import_batch")
    op.drop_table("import_batch")
