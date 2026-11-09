"""tax_rule_set versioned parameter table (issue #81).

Revision ID: 0016_tax_rule_set
Revises: 0015_employment
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_tax_rule_set"
down_revision: str | None = "0015_employment"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tax_rule_set",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tax_year", sa.String(9), nullable=False),
        sa.Column("jurisdiction",
                  sa.Enum("england_wales", "scotland", "northern_ireland",
                          name="taxjurisdiction"),
                  nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("params", sa.String(8000), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("tax_year", "jurisdiction", "version",
                            name="uq_ruleset_year_jur_ver"),
    )


def downgrade() -> None:
    op.drop_table("tax_rule_set")
