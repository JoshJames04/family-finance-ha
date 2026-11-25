"""Record reviewed statement identity links without rewriting ledger history."""
import sqlalchemy as sa
from alembic import op

revision = "0076_statement_bank_identity"
down_revision = "0075_merge_seasonal_payroll"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("statement_bank_identity",
        sa.Column("transaction_id", sa.Integer(),
                  sa.ForeignKey("transaction.id"), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("bank_ref", sa.String(68), nullable=False),
        sa.Column("import_id", sa.Integer(), sa.ForeignKey("statement_import.id"), nullable=False),
        sa.UniqueConstraint("account_id", "bank_ref", name="uq_statement_bank_ref"))


def downgrade() -> None:
    op.drop_table("statement_bank_identity")
