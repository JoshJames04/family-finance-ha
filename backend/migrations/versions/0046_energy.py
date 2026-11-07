"""Energy tariff + daily consumption readings (issue #103).

Revision ID: 0046_energy
Revises: 0045_sync_monitoring
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_energy"
down_revision: str | None = "0045_sync_monitoring"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "energy_tariff",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("unit_rate_millipence", sa.Integer(), nullable=False),
        sa.Column("standing_charge_pence", sa.Integer(), nullable=False),
        sa.CheckConstraint("unit_rate_millipence >= 0",
                           name="ck_tariff_unit"),
        sa.CheckConstraint("standing_charge_pence >= 0",
                           name="ck_tariff_standing"),
        sa.UniqueConstraint("household_id", "effective_from",
                            name="uq_tariff_eff"),
    )
    op.create_index("ix_energy_tariff_household_id", "energy_tariff",
                    ["household_id"])
    op.create_table(
        "energy_reading",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("wh", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.CheckConstraint("wh >= 0", name="ck_reading_wh"),
        sa.UniqueConstraint("household_id", "date",
                            name="uq_reading_day"),
    )
    op.create_index("ix_energy_reading_household_id", "energy_reading",
                    ["household_id"])
    op.create_table(
        "energy_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False,
                  unique=True),
        sa.Column("meter_entity", sa.String(200), nullable=True),
        sa.Column("budget_category_id", sa.Integer(),
                  sa.ForeignKey("category.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("energy_config")
    op.drop_index("ix_energy_reading_household_id", "energy_reading")
    op.drop_table("energy_reading")
    op.drop_index("ix_energy_tariff_household_id", "energy_tariff")
    op.drop_table("energy_tariff")
