"""Optional explicit occasion dates; never infer dates for historical events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071_seasonal_event_date"
down_revision: str | None = "0070_transfer_confirmation_history"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("seasonal_event", sa.Column("event_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("seasonal_event") as batch:
        batch.drop_column("event_date")
