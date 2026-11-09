"""budget_template tables + budget.copied_from_id (issue #59).

Revision ID: 0012_budget_templates
Revises: 0011_revision_reason
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_budget_templates"
down_revision: str | None = "0011_revision_reason"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budget_template",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False,
                  index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("household_id", "name",
                            name="uq_template_household_name"),
    )
    op.create_table(
        "budget_template_line",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("template_id", sa.Integer(),
                  sa.ForeignKey("budget_template.id"), nullable=False,
                  index=True),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("category.id"), nullable=False),
        sa.Column("side",
                  sa.Enum("income", "expense", name="budgetlineside"),
                  nullable=False),
        sa.Column("amount_pence", sa.Integer(), nullable=False),
        sa.UniqueConstraint("template_id", "category_id",
                            name="uq_tline_template_category"),
    )
    with op.batch_alter_table("budget") as batch:
        batch.add_column(
            sa.Column("copied_from_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("from_template_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_budget_copied_from", "budget",
            ["copied_from_id"], ["id"])
        batch.create_foreign_key(
            "fk_budget_template", "budget_template",
            ["from_template_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("budget") as batch:
        batch.drop_column("from_template_id")
        batch.drop_column("copied_from_id")
    op.drop_table("budget_template_line")
    op.drop_table("budget_template")
