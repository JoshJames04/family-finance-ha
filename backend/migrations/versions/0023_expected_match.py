"""expected_match: real transfer legs matched to expected movements
(issue #87).

Revision ID: 0023_expected_match
Revises: 0022_payroll_override
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_expected_match"
down_revision: str | None = "0022_payroll_override"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "expected_match",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("movement_id", sa.Integer(),
                  sa.ForeignKey("expected_movement.id",
                                name="fk_expected_match_movement"),
                  nullable=False, index=True),
        sa.Column("transaction_id", sa.Integer(),
                  sa.ForeignKey("transaction.id",
                                name="fk_expected_match_transaction"),
                  nullable=False, index=True),
        sa.Column("matched_pence", sa.Integer(), nullable=False),
        sa.Column("manual", sa.Boolean(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("movement_id", "transaction_id",
                            name="uq_expected_match_pair"),
        sa.CheckConstraint("matched_pence > 0",
                           name="ck_expected_match_positive"),
    )


def downgrade() -> None:
    op.drop_table("expected_match")
