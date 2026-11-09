"""scenario + scenario_op tables (issue #76).

Sandbox what-if layer: scenarios persist their ops separately from
expected_movement so scenario play can never touch real data.
``scenario_op.expected_movement_id`` is deliberately NOT a foreign
key — expected movements are re-materialised (delete+insert), and a
stale reference must degrade to a no-op, not a constraint violation.

Revision ID: 0034_scenario
Revises: 0033_projection
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_scenario"
down_revision: str | None = "0033_projection"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenario",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("household_id", "name",
                            name="uq_scenario_household_name"),
    )
    op.create_index("ix_scenario_household_id", "scenario",
                    ["household_id"])
    op.create_table(
        "scenario_op",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(),
                  sa.ForeignKey("scenario.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.Enum("add", "remove", "shift",
                                  name="scenariopkind"),
                  nullable=False),
        sa.Column("expected_movement_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("account.id"), nullable=True),
        sa.Column("expected_date", sa.Date(), nullable=True),
        sa.Column("amount_pence", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_scenario_op_scenario_id", "scenario_op",
                    ["scenario_id"])


def downgrade() -> None:
    op.drop_table("scenario_op")
    op.drop_table("scenario")
