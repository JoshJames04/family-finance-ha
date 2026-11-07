"""employment + salary_revision + tax_code_revision (issue #78).

Revision ID: 0015_employment
Revises: 0014_audit_append_only
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_employment"
down_revision: str | None = "0014_audit_append_only"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "employment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(),
                  sa.ForeignKey("household.id"), nullable=False,
                  index=True),
        sa.Column("profile_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id"), nullable=False,
                  index=True),
        sa.Column("employer", sa.String(200), nullable=False),
        sa.Column("jurisdiction",
                  sa.Enum("england_wales", "scotland", "northern_ireland",
                          name="taxjurisdiction"),
                  nullable=False),
        sa.Column("ni_category", sa.String(2), nullable=False,
                  server_default="A"),
        sa.Column("student_loan_plan",
                  sa.Enum("none", "plan1", "plan2", "plan4", "plan5",
                          "postgrad", name="studentloanplan"),
                  nullable=False, server_default="none"),
        sa.Column("pension_scheme_ref", sa.String(128), nullable=True),
        sa.Column("contracted_hours", sa.Numeric(5, 2), nullable=False),
        sa.Column("payday_rule", sa.String(400), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(),
                  server_default=sa.func.now()),
    )
    op.create_table(
        "salary_revision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employment_id", sa.Integer(),
                  sa.ForeignKey("employment.id"), nullable=False,
                  index=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("annual_salary_pence", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(2000), nullable=True),
        sa.UniqueConstraint("employment_id", "effective_date",
                            name="uq_salaryrev_emp_date"),
        sa.CheckConstraint("annual_salary_pence >= 0",
                           name="ck_salaryrev_nonneg"),
    )
    op.create_table(
        "tax_code_revision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employment_id", sa.Integer(),
                  sa.ForeignKey("employment.id"), nullable=False,
                  index=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("tax_code", sa.String(12), nullable=False),
        sa.Column("notes", sa.String(2000), nullable=True),
        sa.UniqueConstraint("employment_id", "effective_date",
                            name="uq_taxrev_emp_date"),
    )


def downgrade() -> None:
    op.drop_table("tax_code_revision")
    op.drop_table("salary_revision")
    op.drop_table("employment")
