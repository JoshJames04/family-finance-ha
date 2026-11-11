"""backfill payroll_run.rule_set_year for pre-column rows.

Runs created before the column existed carry NULL even though
``tax_year`` holds the year the rules were selected for. ``version``
is scoped per (tax_year, jurisdiction) — UK sets ship as version 1
each year — so it cannot disambiguate alone. Backfill only where a
matching rule set provably exists for (tax_year, version,
employment.jurisdiction); stale-rules forecasts whose tax year has
no published set stay NULL rather than record a wrong year.

Revision ID: 0067_backfill_rule_set_year
Revises: 0066_provider_and_income
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0067_backfill_rule_set_year"
down_revision: str | None = "0066_provider_and_income"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE payroll_run SET rule_set_year = tax_year"
        " WHERE rule_set_year IS NULL AND rule_set_version > 0"
        " AND EXISTS ("
        "  SELECT 1 FROM tax_rule_set trs"
        "  JOIN employment e ON e.jurisdiction = trs.jurisdiction"
        "  WHERE e.id = payroll_run.employment_id"
        "    AND trs.tax_year = payroll_run.tax_year"
        "    AND trs.version = payroll_run.rule_set_version)")


def downgrade() -> None:
    # Cannot distinguish backfilled values from originally written
    # ones — leave the data; the column stays populated.
    pass
