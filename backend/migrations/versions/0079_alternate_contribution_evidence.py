"""Record explicit acceptance of a transfer into another shared account."""

import sqlalchemy as sa
from alembic import op

revision: str = "0079_alternate_contribution_evidence"
down_revision: str = "0078_shared_contribution_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expected_match", sa.Column(
        "alternate_destination", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    with op.batch_alter_table("expected_match") as batch:
        batch.drop_column("alternate_destination")
