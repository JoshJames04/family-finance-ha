"""Retain transfer endpoints independently of deleted scheduling rules."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070_transfer_confirmation_history"
down_revision: str | None = "0069_scoped_categories"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("expected_movement", sa.Column(
        "transfer_source_account_id", sa.Integer(), nullable=True))
    op.add_column("expected_movement", sa.Column(
        "transfer_dest_account_id", sa.Integer(), nullable=True))
    # Retained movements refer to deleted rules by source_id. Never give a new
    # rule that historical identity, including IDs deleted before this upgrade.
    with op.batch_alter_table("transfer_rule", recreate="always",
                              table_kwargs={"sqlite_autoincrement": True}):
        pass
    op.execute(sa.text("DELETE FROM sqlite_sequence WHERE name = 'transfer_rule'"))
    op.execute(sa.text(
        "INSERT INTO sqlite_sequence (name, seq) "
        "SELECT 'transfer_rule', MAX(value) FROM ("
        "SELECT COALESCE(MAX(id), 0) AS value FROM transfer_rule UNION ALL "
        "SELECT COALESCE(MAX(source_id), 0) FROM expected_movement "
        "WHERE source_type = 'transfer_rule')"))


def downgrade() -> None:
    with op.batch_alter_table("transfer_rule", recreate="always",
                              table_kwargs={"sqlite_autoincrement": False}):
        pass
    with op.batch_alter_table("expected_movement") as batch:
        batch.drop_column("transfer_dest_account_id")
        batch.drop_column("transfer_source_account_id")
