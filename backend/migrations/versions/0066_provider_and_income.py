"""provider-category map + payroll income association (issues #453, #454).

- transaction.provider_category retains the feed's own classification
  (e.g. Starling spendingCategory) verbatim for audit/remapping.
- provider_category_map holds the household-owned provider-enum →
  category mapping applied at ingest/settle.
- employment.paid_into_account_id names the account net pay lands in.
- expected_movement.source_type gains 'employment' (enum rebuild) for
  paydays projected from the rule before a run exists.

Revision ID: 0066_provider_and_income
Revises: 0065_drop_workbook_import
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_provider_and_income"
down_revision: str | None = "0065_drop_workbook_import"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_NEW_SOURCES = ("commitment", "payroll_run", "transfer_rule",
                "savings_goal", "subscription", "employment", "manual")
_OLD_SOURCES = ("commitment", "payroll_run", "transfer_rule",
                "savings_goal", "subscription", "manual")


def upgrade() -> None:
    with op.batch_alter_table("transaction") as b:
        b.add_column(sa.Column("provider_category", sa.String(64),
                               nullable=True))
    op.create_table(
        "provider_category_map",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id",
                                name="fk_provcategorymap_household"),
                  nullable=False, index=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_category", sa.String(64), nullable=False),
        sa.Column("category_id", sa.Integer(),
                  sa.ForeignKey("category.id",
                                name="fk_provcategorymap_category"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.UniqueConstraint("household_id", "provider",
                            "provider_category",
                            name="uq_provider_cat_map"),
    )
    with op.batch_alter_table("employment") as b:
        b.add_column(sa.Column(
            "paid_into_account_id", sa.Integer(),
            sa.ForeignKey("account.id",
                          name="fk_employment_paid_into_account"),
            nullable=True))
    with op.batch_alter_table("expected_movement") as b:
        b.alter_column(
            "source_type",
            type_=sa.Enum(*_NEW_SOURCES, name="expectedsource"),
            existing_nullable=False)


def downgrade() -> None:
    # Rows sourced by projected employment paydays would violate the
    # old enum — remove them before shrinking it.
    op.execute("DELETE FROM expected_movement "
               "WHERE source_type = 'employment'")
    with op.batch_alter_table("expected_movement") as b:
        b.alter_column(
            "source_type",
            type_=sa.Enum(*_OLD_SOURCES, name="expectedsource"),
            existing_nullable=False)
    with op.batch_alter_table("employment") as b:
        b.drop_column("paid_into_account_id")
    op.drop_table("provider_category_map")
    with op.batch_alter_table("transaction") as b:
        b.drop_column("provider_category")
