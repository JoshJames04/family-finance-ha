"""categorisation_rule table + transaction.suggested_category_id
(issue #68).

Rules suggest categories on incoming transactions — flagged on the
row until confirmed unless the rule is marked auto_apply.

Revision ID: 0036_categorisation
Revises: 0035_notifications
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_categorisation"
down_revision: str | None = "0035_notifications"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categorisation_rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("pattern", sa.String(length=200), nullable=False),
        sa.Column("match_type",
                  sa.Enum("contains", "exact", "regex",
                          name="matchtype"),
                  nullable=False, server_default="contains"),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("category.id"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False,
                  server_default="100"),
        sa.Column("auto_apply", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("times_confirmed", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("source", sa.Enum("manual", "learned",
                                    name="rulesource"),
                  nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
    )
    op.create_index("ix_categorisation_rule_household_id",
                    "categorisation_rule", ["household_id"])
    with op.batch_alter_table("transaction") as batch:
        batch.add_column(
            sa.Column("suggested_category_id", sa.Integer(),
                      sa.ForeignKey(
                          "category.id",
                          name="fk_transaction_suggested_category"),
                      nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transaction") as batch:
        batch.drop_column("suggested_category_id")
    op.drop_table("categorisation_rule")
