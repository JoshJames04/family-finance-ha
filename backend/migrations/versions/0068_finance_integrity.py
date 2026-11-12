"""Finance attribution guards and seasonal private ownership.

Keep historical rows intact; guards prevent new duplicate attribution even
when an older install already has duplicates requiring an owner's review.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068_finance_integrity"
down_revision: str | None = "0067_backfill_rule_set_year"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("seasonal_event") as batch:
        batch.add_column(sa.Column("owner_profile_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_seasonal_owner", "user_profile", ["owner_profile_id"], ["id"])
        batch.create_index("ix_seasonal_event_owner_profile_id", ["owner_profile_id"])
        batch.drop_constraint("uq_seasonal_event_household_type_year", type_="unique")
    op.create_index("uq_seasonal_shared_type_year", "seasonal_event",
                    ["household_id", "type", "year"], unique=True,
                    sqlite_where=sa.text("owner_profile_id IS NULL"))
    op.create_index("uq_seasonal_private_type_year", "seasonal_event",
                    ["household_id", "type", "year", "owner_profile_id"], unique=True,
                    sqlite_where=sa.text("owner_profile_id IS NOT NULL"))
    for event in ("INSERT", "UPDATE"):
        suffix = event.lower()
        repayment_event = event if event == "INSERT" else "UPDATE OF transaction_id"
        payroll_event = event if event == "INSERT" else (
            "UPDATE OF employment_id, period_start, period_end, corrects_id")
        op.execute(f"""CREATE TRIGGER prevent_duplicate_repayment_{suffix}
            BEFORE {repayment_event} ON repayment
            WHEN NEW.transaction_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM repayment r WHERE r.transaction_id = NEW.transaction_id
                AND r.id != NEW.id)
            BEGIN SELECT RAISE(ABORT, 'transaction already attributed to a repayment'); END""")
        op.execute(f"""CREATE TRIGGER prevent_duplicate_payroll_{suffix}
            BEFORE {payroll_event} ON payroll_run
            WHEN NEW.corrects_id IS NULL AND EXISTS (
                SELECT 1 FROM payroll_run r WHERE r.employment_id = NEW.employment_id
                AND r.period_start = NEW.period_start AND r.period_end = NEW.period_end
                AND r.corrects_id IS NULL AND r.id != NEW.id)
            BEGIN SELECT RAISE(ABORT, 'ordinary payroll run already exists'); END""")


def downgrade() -> None:
    for event in ("insert", "update"):
        op.execute(f"DROP TRIGGER prevent_duplicate_repayment_{event}")
        op.execute(f"DROP TRIGGER prevent_duplicate_payroll_{event}")
    with op.batch_alter_table("seasonal_event") as batch:
        batch.drop_index("uq_seasonal_shared_type_year")
        batch.drop_index("uq_seasonal_private_type_year")
        batch.drop_index("ix_seasonal_event_owner_profile_id")
        batch.drop_constraint("fk_seasonal_owner", type_="foreignkey")
        batch.drop_column("owner_profile_id")
        batch.create_unique_constraint("uq_seasonal_event_household_type_year",
                                       ["household_id", "type", "year"])
