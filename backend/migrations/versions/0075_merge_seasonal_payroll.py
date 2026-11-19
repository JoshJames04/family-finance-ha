"""Join seasonal dates and verified payroll/bonus migration branches."""

revision: str = "0075_merge_seasonal_payroll"
down_revision: tuple[str, str] = ("0071_seasonal_event_date", "0074_employer_bonuses")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
