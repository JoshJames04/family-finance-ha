"""Store household contribution proposals in scoped rows."""

import sqlalchemy as sa
from alembic import op

revision: str = "0078_shared_contribution_proposals"
down_revision: str = "0075_merge_seasonal_payroll"
branch_labels = None
depends_on = None


_KINDS = (
    "spend_above", "balance_below", "weekly_digest", "payday_reminder",
    "bill_reminder", "low_projected_balance", "budget_overspend",
    "unusual_transaction", "price_change", "missed_transfer", "sync_failure",
    "payroll_variance", "payslip_review",
)
_NEW_KIND = "shared_contribution_change"


def upgrade() -> None:
    # SQLite's existing enum column is a VARCHAR. Widen it explicitly.
    with op.batch_alter_table("notification_rule") as batch:
        batch.alter_column("kind", type_=sa.Enum(*_KINDS, _NEW_KIND, name="notificationkind"),
                           existing_nullable=False)
    op.create_table(
        "shared_contribution_proposal",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("household.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_shared_contribution_proposal_scope", "shared_contribution_proposal",
                    ["household_id", "account_id"])


def downgrade() -> None:
    op.drop_index("ix_shared_contribution_proposal_scope", "shared_contribution_proposal")
    op.drop_table("shared_contribution_proposal")
    rules = "SELECT id FROM notification_rule WHERE kind = 'shared_contribution_change'"
    op.execute(f"DELETE FROM notification_event WHERE rule_id IN ({rules})")
    op.execute(f"DELETE FROM notification_delivery WHERE rule_id IN ({rules})")
    op.execute(f"DELETE FROM notification_rule WHERE id IN ({rules})")
    with op.batch_alter_table("notification_rule") as batch:
        batch.alter_column("kind", type_=sa.Enum(*_KINDS, name="notificationkind"),
                           existing_nullable=False)
