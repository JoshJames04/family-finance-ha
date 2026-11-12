"""Scope explicit categorisation rules to account and signed amount."""
from alembic import op
import sqlalchemy as sa

revision = '0069_scoped_categories'
down_revision = '0068_finance_integrity'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('categorisation_rule') as batch:
        batch.add_column(sa.Column('account_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('transfer_account_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_categorisation_transfer_account', 'account', ['transfer_account_id'], ['id'])
        batch.add_column(sa.Column('amount_pence', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_categorisation_account', 'account', ['account_id'], ['id'])


def downgrade():
    with op.batch_alter_table('categorisation_rule') as batch:
        batch.drop_constraint('fk_categorisation_account', type_='foreignkey')
        batch.drop_constraint('fk_categorisation_transfer_account', type_='foreignkey')
        batch.drop_column('transfer_account_id')
        batch.drop_column('amount_pence')
        batch.drop_column('account_id')
