"""core schema: household, user_profile, account, category

Revision ID: 0002_core_schema
Revises: 0001_app_meta
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_core_schema"
down_revision = "0001_app_meta"
branch_labels = None
depends_on = None

role_enum = sa.Enum("admin", "member", "viewer", name="role")
account_type_enum = sa.Enum(
    "personal", "joint", "bills", "savings", "cash", "credit", "loan",
    name="accounttype",
)


def upgrade() -> None:
    op.create_table(
        "household",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("payday_day", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "user_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False, index=True),
        sa.Column("ha_user_id", sa.String(64), nullable=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("ha_user_id", name="uq_profile_ha_user"),
    )
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("type", account_type_enum, nullable=False),
        sa.Column("owner_profile_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id"), nullable=True, index=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("opening_balance_pence", sa.Integer(), nullable=False),
        sa.Column("opening_date", sa.Date(), nullable=False),
        sa.Column("institution_ref", sa.String(128), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("household_id", "name",
                            name="uq_account_household_name"),
    )
    op.create_table(
        "category",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False, index=True),
        sa.Column("parent_id", sa.Integer(),
                  sa.ForeignKey("category.id"), nullable=True, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.CheckConstraint("id <> parent_id",
                           name="ck_category_not_own_parent"),
        sa.UniqueConstraint("household_id", "parent_id", "name",
                            name="uq_category_sibling_name"),
    )


def downgrade() -> None:
    op.drop_table("category")
    op.drop_table("account")
    op.drop_table("user_profile")
    op.drop_table("household")
