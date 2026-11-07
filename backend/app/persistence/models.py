"""ORM models — platform and core financial schema (Epic 3 slice 1).

Money is stored as integer minor units (pence) per ADR-0003. The domain
layer's Money type is the only legal currency representation in logic and
at API boundaries.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from datetime import date as date_t
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.persistence.base import Base


class AppMeta(Base):
    """Key-value platform state (schema bookkeeping, last snapshot, etc.)."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(1024), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Role(enum.StrEnum):
    admin = "admin"
    member = "member"
    viewer = "viewer"


class AccountType(enum.StrEnum):
    personal = "personal"
    joint = "joint"
    bills = "bills"
    savings = "savings"
    cash = "cash"
    credit = "credit"
    loan = "loan"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())


class Household(TimestampMixin, Base):
    __tablename__ = "household"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    payday_day: Mapped[int | None] = mapped_column(Integer, nullable=True)

    members: Mapped[list[UserProfile]] = relationship(
        back_populates="household", cascade="all, delete-orphan")
    accounts: Mapped[list[Account]] = relationship(
        back_populates="household")
    categories: Mapped[list[Category]] = relationship(
        back_populates="household")


class UserProfile(TimestampMixin, Base):
    """Application-level identity mapped from an HA user (or standalone)."""

    __tablename__ = "user_profile"
    __table_args__ = (
        UniqueConstraint("ha_user_id", name="uq_profile_ha_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    ha_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.member,
                                       nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True,
                                            nullable=False)
    password_hash: Mapped[str | None] = mapped_column(
        String(200), nullable=True)  # standalone-mode credential

    household: Mapped[Household] = relationship(back_populates="members")
    owned_accounts: Mapped[list[Account]] = relationship(
        back_populates="owner")


class Account(TimestampMixin, Base):
    """Financial account. owner_profile NULL = shared household account."""

    __tablename__ = "account"
    __table_args__ = (
        UniqueConstraint("household_id", "name", name="uq_account_household_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[AccountType] = mapped_column(Enum(AccountType),
                                             nullable=False)
    owner_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_profile.id"), nullable=True, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="GBP",
                                          nullable=False)
    opening_balance_pence: Mapped[int] = mapped_column(Integer, default=0,
                                                     nullable=False)
    opening_date: Mapped[date] = mapped_column(Date, nullable=False)
    institution_ref: Mapped[str | None] = mapped_column(String(128),
                                                      nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False,
                                           nullable=False)
    # NULL = no threshold; set → projection warns when the daily
    # series dips below (issue #74).
    min_balance_pence: Mapped[int | None] = mapped_column(
        Integer, nullable=True)

    household: Mapped[Household] = relationship(back_populates="accounts")
    owner: Mapped[UserProfile | None] = relationship(
        back_populates="owned_accounts")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="account")


class Category(TimestampMixin, Base):
    """Hierarchical category. Cycles are impossible: a parent assignment is
    only valid when the candidate parent is not a descendant of the node —
    enforced by domain.categories (DB cannot express recursion)."""

    __tablename__ = "category"
    __table_args__ = (
        CheckConstraint("id <> parent_id", name="ck_category_not_own_parent"),
        UniqueConstraint("household_id", "parent_id", "name",
                         name="uq_category_sibling_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False,
                                            nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False,
                                           nullable=False)

    household: Mapped[Household] = relationship(back_populates="categories")
    parent: Mapped[Category | None] = relationship(
        remote_side=[id], back_populates="children")
    children: Mapped[list[Category]] = relationship(
        back_populates="parent")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="category",
        foreign_keys="Transaction.category_id")


class TransactionStatus(enum.StrEnum):
    pending = "pending"
    cleared = "cleared"
    reconciled = "reconciled"
    void = "void"


class TransactionSource(enum.StrEnum):
    manual = "manual"
    starling = "starling"
    excel_import = "excel_import"
    system = "system"
    manual_verification = "manual_verification"


class Transaction(TimestampMixin, Base):
    """Signed-pence ledger row. History is never edited — corrections
    supersede. `transfer_id`/`import_record_id` are plain ints until their
    tables land (Epic 3/4), when FKs are added."""

    __tablename__ = "transaction"
    __table_args__ = (
        UniqueConstraint("account_id", "source", "external_ref",
                         name="uq_transaction_dedup"),
        CheckConstraint(
            "status IN ('pending','cleared','reconciled','void')",
            name="ck_transaction_status"),
        CheckConstraint(
            "source IN ('manual','starling','excel_import','system')",
            name="ck_transaction_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True)
    date: Mapped[date_t] = mapped_column(Date, nullable=False)
    settled_date: Mapped[date_t | None] = mapped_column(Date, nullable=True)
    amount_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    payee: Mapped[str] = mapped_column(String(200), nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True, index=True)
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus), default=TransactionStatus.pending,
        nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    source: Mapped[TransactionSource] = mapped_column(
        Enum(TransactionSource), default=TransactionSource.manual,
        nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(128),
                                                     nullable=True)
    import_record_id: Mapped[int | None] = mapped_column(Integer,
                                                         nullable=True)
    transfer_id: Mapped[int | None] = mapped_column(
        ForeignKey("transfer.id"), nullable=True, index=True)
    refund_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("transaction.id"), nullable=True, index=True)
    suggested_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True)

    account: Mapped[Account] = relationship(back_populates="transactions")
    category: Mapped[Category | None] = relationship(
        back_populates="transactions",
        foreign_keys="Transaction.category_id")
    suggested_category: Mapped[Category | None] = relationship(
        foreign_keys="Transaction.suggested_category_id")
    transfer: Mapped[Transfer | None] = relationship(
        back_populates="legs")
    refund_of: Mapped[Transaction | None] = relationship(
        remote_side=[id], back_populates="refunds")
    refunds: Mapped[list[Transaction]] = relationship(
        back_populates="refund_of")


class Transfer(TimestampMixin, Base):
    """A money movement between two accounts, modelled as two linked
    transaction legs (one debit, one credit). Sum of legs is always 0 —
    enforced by domain.transfers.create_transfer. Transfers are excluded
    from income/expense by query contract (transfer_id IS NOT NULL)."""

    __tablename__ = "transfer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    rule_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=True,
                                          nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    household: Mapped[Household] = relationship()
    legs: Mapped[list[Transaction]] = relationship(back_populates="transfer")


class Frequency(enum.StrEnum):
    weekly = "weekly"
    fortnightly = "fortnightly"
    monthly = "monthly"
    quarterly = "quarterly"
    annually = "annually"


class RecurringCommitment(TimestampMixin, Base):
    """A scheduled obligation/income. Fixed pence amount OR a formula
    string (e.g. payroll net) — exactly one required (CHECK). End
    conditions optional: end_date and/or occurrence_count."""

    __tablename__ = "recurring_commitment"
    __table_args__ = (
        CheckConstraint(
            "(amount_pence IS NOT NULL) <> (formula IS NOT NULL)",
            name="ck_recurring_amount_xor_formula"),
        CheckConstraint(
            "frequency IN ('weekly','fortnightly','monthly','quarterly',"
            "'annually')", name="ck_recurring_frequency"),
        CheckConstraint(
            "day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31",
            name="ck_recurring_dom"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True)
    amount_pence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    formula: Mapped[str | None] = mapped_column(String(200), nullable=True)
    frequency: Mapped[Frequency] = mapped_column(Enum(Frequency),
                                                 nullable=False)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    last_generated: Mapped[date_t | None] = mapped_column(Date,
                                                          nullable=True)
    end_date: Mapped[date_t | None] = mapped_column(Date, nullable=True)
    occurrence_count: Mapped[int | None] = mapped_column(Integer,
                                                         nullable=True)
    generated_count: Mapped[int] = mapped_column(Integer, default=0,
                                                 nullable=False)
    source: Mapped[TransactionSource] = mapped_column(
        Enum(TransactionSource), default=TransactionSource.manual,
        nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True,
                                         nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    household: Mapped[Household] = relationship()
    account: Mapped[Account] = relationship()
    category: Mapped[Category | None] = relationship()


class ReportingPeriod(TimestampMixin, Base):
    """A closed calendar window (e.g. "May 2026"). Locked periods freeze
    budget originals (Epic 5) and make contained transactions editable
    only via corrections."""

    __tablename__ = "reporting_period"
    __table_args__ = (
        UniqueConstraint("household_id", "label",
                         name="uq_period_household_label"),
        CheckConstraint("end_date >= start_date",
                        name="ck_period_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    start_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    end_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False,
                                         nullable=False)

    household: Mapped[Household] = relationship()


class AuditEvent(Base):
    """Append-only mutation log (Epic 2 consumer: audit middleware/report)."""

    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False,
                                            index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())


class ImportStatus(enum.StrEnum):
    dry_run = "dry_run"
    committed = "committed"
    rolled_back = "rolled_back"


class RowClass(enum.StrEnum):
    actual = "actual"
    forecast_overwritten = "forecast_overwritten"
    aggregate = "aggregate"
    unknown = "unknown"


class ImportBatch(Base):
    """One workbook import attempt — dry_run, committed, or rolled_back."""

    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False,
                                        index=True)
    file_mtime: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus), default=ImportStatus.dry_run, nullable=False)
    rows_read: Mapped[int] = mapped_column(Integer, default=0,
                                           nullable=False)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0,
                                               nullable=False)
    rows_flagged: Mapped[int] = mapped_column(Integer, default=0,
                                              nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())
    committed_at: Mapped[datetime | None] = mapped_column(DateTime,
                                                          nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime,
                                                            nullable=True)

    records: Mapped[list[ImportRecord]] = relationship(
        back_populates="batch", cascade="all, delete-orphan")


class ImportRecord(Base):
    """Per-cell provenance: sheet/cell, raw + interpreted, classification,
    target ref, review flag. Never dropped — survives rollback."""

    __tablename__ = "import_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batch.id"), nullable=False, index=True)
    sheet: Mapped[str] = mapped_column(String(120), nullable=False)
    cell_range: Mapped[str] = mapped_column(String(40), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(String(2000),
                                                  nullable=True)
    interpreted_value: Mapped[str | None] = mapped_column(String(2000),
                                                          nullable=True)
    classification: Mapped[RowClass] = mapped_column(
        Enum(RowClass), nullable=False, index=True)
    target_entity: Mapped[str | None] = mapped_column(String(40),
                                                      nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_flag: Mapped[bool] = mapped_column(Boolean, default=False,
                                              nullable=False)

    batch: Mapped[ImportBatch] = relationship(back_populates="records")


class MigrationFlag(Base):
    """Defect register outcome (A–F) per batch."""

    __tablename__ = "migration_flag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batch.id"), nullable=False, index=True)
    defect_id: Mapped[str] = mapped_column(String(4), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="detected",
                                        nullable=False)
    detail: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(String(2000),
                                                      nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())


class BudgetPeriodType(enum.StrEnum):
    monthly = "monthly"
    annual = "annual"
    custom = "custom"


class BudgetStatus(enum.StrEnum):
    draft = "draft"
    approved = "approved"
    locked = "locked"


class BudgetLineSide(enum.StrEnum):
    income = "income"
    expense = "expense"


class Budget(TimestampMixin, Base):
    """A household/member budget over a period (issue #57).

    draft → approved → locked. Approval freezes line originals; a locked
    reporting period makes the budget read-only everywhere."""

    __tablename__ = "budget"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_budget_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    owner_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_profile.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    period_type: Mapped[BudgetPeriodType] = mapped_column(
        Enum(BudgetPeriodType), nullable=False)
    start_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    end_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    status: Mapped[BudgetStatus] = mapped_column(
        Enum(BudgetStatus), default=BudgetStatus.draft, nullable=False)
    copied_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("budget.id"), nullable=True)
    from_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("budget_template.id"), nullable=True)

    household: Mapped[Household] = relationship()
    lines: Mapped[list[BudgetLine]] = relationship(
        back_populates="budget", cascade="all, delete-orphan")


class BudgetLine(TimestampMixin, Base):
    """One category line inside a budget. ``original_pence`` freezes on
    approval; ``revised_pence`` carries post-approval changes."""

    __tablename__ = "budget_line"
    __table_args__ = (
        UniqueConstraint("budget_id", "category_id",
                         name="uq_line_budget_category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    budget_id: Mapped[int] = mapped_column(
        ForeignKey("budget.id"), nullable=False, index=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("category.id"), nullable=False)
    side: Mapped[BudgetLineSide] = mapped_column(
        Enum(BudgetLineSide), nullable=False)
    original_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    revised_pence: Mapped[int | None] = mapped_column(Integer,
                                                      nullable=True)
    revision_reason: Mapped[str | None] = mapped_column(String(200),
                                                        nullable=True)
    rollover: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False)

    budget: Mapped[Budget] = relationship(back_populates="lines")

    @property
    def effective_pence(self) -> int:
        return (self.revised_pence if self.revised_pence is not None
                else self.original_pence)


class BudgetTemplate(TimestampMixin, Base):
    """Named reusable line set (issue #59). Edits never mutate budgets
    already created from it — application copies values, not refs."""

    __tablename__ = "budget_template"
    __table_args__ = (
        UniqueConstraint("household_id", "name",
                         name="uq_template_household_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    household: Mapped[Household] = relationship()
    lines: Mapped[list[BudgetTemplateLine]] = relationship(
        back_populates="template", cascade="all, delete-orphan")


class BudgetTemplateLine(Base):
    __tablename__ = "budget_template_line"
    __table_args__ = (
        UniqueConstraint("template_id", "category_id",
                         name="uq_tline_template_category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("budget_template.id"), nullable=False, index=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("category.id"), nullable=False)
    side: Mapped[BudgetLineSide] = mapped_column(
        Enum(BudgetLineSide), nullable=False)
    amount_pence: Mapped[int] = mapped_column(Integer, nullable=False)

    template: Mapped[BudgetTemplate] = relationship(
        back_populates="lines")


class TaxJurisdiction(enum.StrEnum):
    england_wales = "england_wales"
    scotland = "scotland"
    northern_ireland = "northern_ireland"


class StudentLoanPlan(enum.StrEnum):
    none = "none"
    plan1 = "plan1"
    plan2 = "plan2"
    plan4 = "plan4"
    plan5 = "plan5"
    postgrad = "postgrad"


class PensionMode(enum.StrEnum):
    none = "none"
    relief_at_source = "relief_at_source"  # post-tax; no PAYE/NI effect
    net_pay = "net_pay"                    # reduces taxable, not NI gross
    salary_sacrifice = "salary_sacrifice"  # reduces both tax and NI gross


class Employment(TimestampMixin, Base):
    """A member's employment — payroll facts + payday rule ref (issue
    #78). Salary and tax code are effective-dated revision series, never
    edited in place."""

    __tablename__ = "employment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile.id"), nullable=False, index=True)
    employer: Mapped[str] = mapped_column(String(200), nullable=False)
    jurisdiction: Mapped[TaxJurisdiction] = mapped_column(
        Enum(TaxJurisdiction), nullable=False)
    ni_category: Mapped[str] = mapped_column(
        String(2), default="A", nullable=False)
    student_loan_plan: Mapped[StudentLoanPlan] = mapped_column(
        Enum(StudentLoanPlan), default=StudentLoanPlan.none,
        nullable=False)
    pension_scheme_ref: Mapped[str | None] = mapped_column(
        String(128), nullable=True)
    pension_mode: Mapped[PensionMode] = mapped_column(
        Enum(PensionMode), default=PensionMode.none, nullable=False)
    pension_employee_bps: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False)
    pension_fixed_pence: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False)
    work_days_per_week: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), default=Decimal("5"), server_default="5",
        nullable=False)
    overtime_multipliers: Mapped[str] = mapped_column(
        String(400), default="{}", server_default="{}", nullable=False)
    contracted_hours: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False)
    payday_rule: Mapped[str | None] = mapped_column(
        String(400), nullable=True)  # serialised domain.paydays.PaydayRule
    is_active: Mapped[bool] = mapped_column(Boolean, default=True,
                                            nullable=False)

    profile: Mapped[UserProfile] = relationship()
    salary_revisions: Mapped[list[SalaryRevision]] = relationship(
        back_populates="employment",
        order_by="SalaryRevision.effective_date")
    tax_code_revisions: Mapped[list[TaxCodeRevision]] = relationship(
        back_populates="employment",
        order_by="TaxCodeRevision.effective_date")


class SalaryRevision(Base):
    """Effective-dated annual salary (pence). Same-date duplicates are
    rejected — a later revision supersedes an earlier one, never
    overlaps."""

    __tablename__ = "salary_revision"
    __table_args__ = (
        UniqueConstraint("employment_id", "effective_date",
                         name="uq_salaryrev_emp_date"),
        CheckConstraint("annual_salary_pence >= 0",
                        name="ck_salaryrev_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employment_id: Mapped[int] = mapped_column(
        ForeignKey("employment.id"), nullable=False, index=True)
    effective_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    annual_salary_pence: Mapped[int] = mapped_column(Integer,
                                                   nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000),
                                              nullable=True)

    employment: Mapped[Employment] = relationship(
        back_populates="salary_revisions")


class TaxCodeRevision(Base):
    """Effective-dated tax code (issue #78 — tax code is effective-dated
    on the employment, modelled as its own series)."""

    __tablename__ = "tax_code_revision"
    __table_args__ = (
        UniqueConstraint("employment_id", "effective_date",
                         name="uq_taxrev_emp_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employment_id: Mapped[int] = mapped_column(
        ForeignKey("employment.id"), nullable=False, index=True)
    effective_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    tax_code: Mapped[str] = mapped_column(String(12), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000),
                                              nullable=True)

    employment: Mapped[Employment] = relationship(
        back_populates="tax_code_revisions")


class TaxRuleSet(Base):
    """Versioned UK statutory parameters per tax year × jurisdiction
    (issue #81). Rows are immutable — a new year or correction lands as
    a new (year, jurisdiction, version) row, never an edit."""

    __tablename__ = "tax_rule_set"
    __table_args__ = (
        UniqueConstraint("tax_year", "jurisdiction", "version",
                         name="uq_ruleset_year_jur_ver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tax_year: Mapped[str] = mapped_column(String(9), nullable=False)
    jurisdiction: Mapped[TaxJurisdiction] = mapped_column(
        Enum(TaxJurisdiction), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[str] = mapped_column(String(8000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())


class OvertimeStatus(enum.StrEnum):
    draft = "draft"
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"


class DayType(enum.StrEnum):
    weekday = "weekday"
    saturday = "saturday"
    sunday = "sunday"
    bank_holiday = "bank_holiday"


class OvertimeEntry(TimestampMixin, Base):
    """A member's overtime claim (issue #79). Multiplier and hourly rate
    are snapshotted at entry creation so a later salary change never
    silently re-rates an approved entry — corrections need a new
    draft→approved cycle."""

    __tablename__ = "overtime_entry"
    __table_args__ = (
        CheckConstraint("hours > 0", name="ck_overtime_hours_pos"),
        CheckConstraint("multiplier_bps > 0",
                        name="ck_overtime_mult_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employment_id: Mapped[int] = mapped_column(
        ForeignKey("employment.id"), nullable=False, index=True)
    work_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    multiplier_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    day_type: Mapped[DayType] = mapped_column(Enum(DayType),
                                              nullable=False)
    status: Mapped[OvertimeStatus] = mapped_column(
        Enum(OvertimeStatus), default=OvertimeStatus.draft,
        nullable=False)
    hourly_rate_pence: Mapped[int] = mapped_column(Integer,
                                                   nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000),
                                              nullable=True)

    employment: Mapped[Employment] = relationship()

    @property
    def pay_pence(self) -> int:
        return int(Decimal(str(self.hours)) * self.hourly_rate_pence
                   * self.multiplier_bps // Decimal(10_000))


class PayrollRunStatus(enum.StrEnum):
    forecast = "forecast"
    reconciled = "reconciled"
    corrected = "corrected"


class PayrollComponentKind(enum.StrEnum):
    base_salary = "base_salary"
    overtime = "overtime"
    bonus = "bonus"
    allowance = "allowance"
    unpaid_leave = "unpaid_leave"
    pension = "pension"
    tax = "tax"
    ni = "ni"
    student_loan = "student_loan"


class PayrollRun(TimestampMixin, Base):
    """One pay period for an employment (issue #82): forecast →
    payslip → reconciled. Components carry forecast vs actual so a
    payslip discrepancy shows exactly which component diverged.
    Reconciled runs are immutable — fixes land via a correcting run
    (`corrects_id`), never an edit."""

    __tablename__ = "payroll_run"
    __table_args__ = (
        CheckConstraint("period_end >= period_start",
                        name="ck_payrun_period"),
        UniqueConstraint("employment_id", "period_start",
                         "period_end", "corrects_id",
                         name="uq_payrun_emp_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employment_id: Mapped[int] = mapped_column(
        ForeignKey("employment.id"), nullable=False, index=True)
    period_start: Mapped[date_t] = mapped_column(Date, nullable=False)
    period_end: Mapped[date_t] = mapped_column(Date, nullable=False)
    pay_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    status: Mapped[PayrollRunStatus] = mapped_column(
        Enum(PayrollRunStatus), default=PayrollRunStatus.forecast,
        nullable=False)
    tax_year: Mapped[str] = mapped_column(String(9), nullable=False)
    rule_set_version: Mapped[int] = mapped_column(Integer,
                                                  nullable=False)
    gross_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    net_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    corrects_id: Mapped[int | None] = mapped_column(
        ForeignKey("payroll_run.id"), nullable=True)

    employment: Mapped[Employment] = relationship()
    components: Mapped[list[PayrollComponent]] = relationship(
        back_populates="run", cascade="all, delete-orphan")


class PayrollComponent(Base):
    """One line of a payroll run — earnings (+), deductions (-)."""

    __tablename__ = "payroll_component"
    __table_args__ = (
        UniqueConstraint("run_id", "kind", "label",
                         name="uq_paycomp_run_kind_label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("payroll_run.id"), nullable=False, index=True)
    kind: Mapped[PayrollComponentKind] = mapped_column(
        Enum(PayrollComponentKind), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    forecast_pence: Mapped[int] = mapped_column(Integer,
                                                nullable=False)
    actual_pence: Mapped[int | None] = mapped_column(Integer,
                                                     nullable=True)

    run: Mapped[PayrollRun] = relationship(
        back_populates="components")

    @property
    def variance_pence(self) -> int | None:
        """actual − forecast; None until the payslip lands."""
        if self.actual_pence is None:
            return None
        return self.actual_pence - self.forecast_pence


class Confidence(enum.StrEnum):
    scheduled = "scheduled"    # contractual/exact — commitments, payslips
    estimated = "estimated"    # forecast — unreconciled payroll runs


class ExpectedSource(enum.StrEnum):
    commitment = "commitment"
    payroll_run = "payroll_run"
    transfer_rule = "transfer_rule"
    savings_goal = "savings_goal"
    subscription = "subscription"
    manual = "manual"


class ExpectedMovement(Base):
    """One dated expected income/expenditure item (issue #72) — the
    forecast substrate. Always traceable: (source_type, source_id,
    expected_date, account_id) is unique, so re-materialisation is a
    delete+insert of FUTURE rows for a source and stays idempotent.
    ``account_id`` is part of the key because a transfer rule emits two
    legs (out on source, in on destination) for the same date."""

    __tablename__ = "expected_movement"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id",
                         "expected_date", "account_id",
                         name="uq_expected_source_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account.id"), nullable=True, index=True)
    expected_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    amount_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    confidence: Mapped[Confidence] = mapped_column(
        Enum(Confidence), nullable=False)
    source_type: Mapped[ExpectedSource] = mapped_column(
        Enum(ExpectedSource), nullable=False)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())


class AmountKind(enum.StrEnum):
    """How a transfer rule derives its amount (issue #86)."""
    fixed = "fixed"            # literal pence each occurrence
    income_pct = "income_pct"  # % (basis points) of income into source
    budget_line = "budget_line"  # covering approved budget's line


class TransferRule(TimestampMixin, Base):
    """A configurable recurring household flow (issue #86):
    personal→bills, joint→food, etc. ``amount_spec`` is a JSON object
    discriminated by ``kind``:

    - ``{"kind": "fixed", "pence": 120000}``
    - ``{"kind": "income_pct", "bps": 3000}`` — 30% of income received
      into the source account in the period before each occurrence
    - ``{"kind": "budget_line", "category_id": 7}`` — the covering
      approved budget's effective line amount for that category

    Materialises two expected-movement legs per occurrence (out on the
    source account, in on the destination)."""

    __tablename__ = "transfer_rule"
    __table_args__ = (
        CheckConstraint("source_account_id != dest_account_id",
                        name="ck_transfer_rule_distinct_accounts"),
        CheckConstraint("day_of_month BETWEEN 1 AND 31",
                        name="ck_transfer_rule_day"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date",
                        name="ck_transfer_rule_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False)
    dest_account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False)
    amount_spec: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[Frequency] = mapped_column(Enum(Frequency),
                                                 nullable=False)
    day_of_month: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    end_date: Mapped[date_t | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True,
                                         nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    source_account: Mapped[Account] = relationship(
        foreign_keys=[source_account_id])
    dest_account: Mapped[Account] = relationship(
        foreign_keys=[dest_account_id])


class PayrollOverride(Base):
    """A run-scoped manual adjustment (issue #85): signed delta against
    a component kind, with mandatory reason + actor. Never carried into
    future forecasts — each override lives on exactly one run."""

    __tablename__ = "payroll_override"
    __table_args__ = (
        CheckConstraint("delta_pence != 0",
                        name="ck_payroll_override_nonzero"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("payroll_run.id"), nullable=False, index=True)
    kind: Mapped[PayrollComponentKind] = mapped_column(
        Enum(PayrollComponentKind), nullable=False)
    delta_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())

    run: Mapped[PayrollRun] = relationship()


class ExpectedMatch(Base):
    """A real transfer leg matched against an expected movement
    (issue #87). Auto-matches land with ``manual=False``; user
    confirmations with ``manual=True``. One leg can match at most one
    movement (enforced in the domain layer)."""

    __tablename__ = "expected_match"
    __table_args__ = (
        UniqueConstraint("movement_id", "transaction_id",
                         name="uq_expected_match_pair"),
        CheckConstraint("matched_pence > 0",
                        name="ck_expected_match_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    movement_id: Mapped[int] = mapped_column(
        ForeignKey("expected_movement.id"), nullable=False, index=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transaction.id"), nullable=False, index=True)
    matched_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    manual: Mapped[bool] = mapped_column(Boolean, default=False,
                                         nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())

    movement: Mapped[ExpectedMovement] = relationship()
    transaction: Mapped[Transaction] = relationship()


class GoalType(enum.StrEnum):
    emergency = "emergency"
    holiday = "holiday"
    christmas = "christmas"
    annual_reserve = "annual_reserve"
    general = "general"


class SavingsGoal(TimestampMixin, Base):
    """A savings target against a linked pot account (issue #91).
    Contributions are DERIVED from real inbound transfer legs — the
    same leg appears in the account view, so nothing is double-booked.
    Dated goals are sinking funds: target ÷ months → monthly need,
    materialised into the expected stream."""

    __tablename__ = "savings_goal"
    __table_args__ = (
        CheckConstraint("target_pence > 0",
                        name="ck_savings_goal_target"),
        CheckConstraint("target_date IS NULL OR target_date >= start_date",
                        name="ck_savings_goal_dates"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    goal_type: Mapped[GoalType] = mapped_column(Enum(GoalType),
                                                nullable=False)
    target_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    target_date: Mapped[date_t | None] = mapped_column(Date,
                                                       nullable=True)
    start_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True,
                                         nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    account: Mapped[Account] = relationship()


class LoanDirection(enum.StrEnum):
    we_owe = "we_owe"      # the household borrowed (a liability)
    they_owe = "they_owe"  # the household lent (an asset)


class Loan(TimestampMixin, Base):
    """A loan with a derived outstanding balance (issue #93).
    Outstanding is ALWAYS computed — principal + simple interest −
    validated repayments — never a stored editable figure (structural
    fix for workbook defect B: a `remaining` cell that could be edited
    to anything)."""

    __tablename__ = "loan"
    __table_args__ = (
        CheckConstraint("principal_pence > 0",
                        name="ck_loan_principal"),
        CheckConstraint(
            "interest_bps_pa IS NULL OR interest_bps_pa >= 0",
            name="ck_loan_interest"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    party: Mapped[str] = mapped_column(String(120), nullable=False)
    direction: Mapped[LoanDirection] = mapped_column(
        Enum(LoanDirection), nullable=False)
    principal_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    expected_payment_pence: Mapped[int | None] = mapped_column(
        Integer, nullable=True)
    interest_bps_pa: Mapped[int | None] = mapped_column(Integer,
                                                      nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True,
                                         nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    repayments: Mapped[list[Repayment]] = relationship(
        back_populates="loan", cascade="all, delete-orphan",
        order_by="Repayment.date")


class Repayment(Base):
    """One payment against a loan — optionally linked to the real
    transaction it was matched to."""

    __tablename__ = "repayment"
    __table_args__ = (
        CheckConstraint("amount_pence > 0",
                        name="ck_repayment_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loan_id: Mapped[int] = mapped_column(
        ForeignKey("loan.id"), nullable=False, index=True)
    date: Mapped[date_t] = mapped_column(Date, nullable=False)
    amount_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transaction.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())

    loan: Mapped[Loan] = relationship(back_populates="repayments")
    transaction: Mapped[Transaction | None] = relationship()


class PurchaseStatus(enum.StrEnum):
    idea = "idea"
    planned = "planned"
    purchased = "purchased"
    wrapped = "wrapped"
    given = "given"


class PaymentStatus(enum.StrEnum):
    unpaid = "unpaid"
    paid = "paid"
    refunded = "refunded"


class SeasonalEvent(TimestampMixin, Base):
    """A yearly event with its own budget — type is free text so a
    second event kind (birthdays, weddings…) needs no schema change
    (issue #96). Optional link to the savings goal funding it."""

    __tablename__ = "seasonal_event"
    __table_args__ = (
        CheckConstraint("budget_pence >= 0",
                        name="ck_seasonal_event_budget"),
        CheckConstraint("year BETWEEN 1900 AND 2200",
                        name="ck_seasonal_event_year"),
        UniqueConstraint("household_id", "type", "year",
                         name="uq_seasonal_event_household_type_year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    budget_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    savings_goal_id: Mapped[int | None] = mapped_column(
        ForeignKey("savings_goal.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    savings_goal: Mapped[SavingsGoal | None] = relationship()
    gifts: Mapped[list[Gift]] = relationship(
        back_populates="event", cascade="all, delete-orphan",
        order_by="Gift.id")


class Gift(Base):
    """One gift idea inside a seasonal event: recipient, planned vs
    actual cost, purchase + payment status, optional transaction link
    once bought (issue #96)."""

    __tablename__ = "gift"
    __table_args__ = (
        CheckConstraint("planned_pence >= 0",
                        name="ck_gift_planned"),
        CheckConstraint("actual_pence IS NULL OR actual_pence >= 0",
                        name="ck_gift_actual"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("seasonal_event.id"), nullable=False, index=True)
    recipient: Mapped[str] = mapped_column(String(120), nullable=False)
    idea: Mapped[str] = mapped_column(String(300), nullable=False)
    planned_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_pence: Mapped[int | None] = mapped_column(Integer,
                                                    nullable=True)
    purchase_status: Mapped[PurchaseStatus] = mapped_column(
        Enum(PurchaseStatus), default=PurchaseStatus.idea,
        nullable=False)
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.unpaid,
        nullable=False)
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transaction.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())

    event: Mapped[SeasonalEvent] = relationship(back_populates="gifts")
    transaction: Mapped[Transaction | None] = relationship()


class BalanceVerification(Base):
    """Asserted-vs-derived balance check (issue #95). Corrections never
    edit history: `correction_txn_id` points at the adjusting ledger row
    the verification created (source `manual_verification`)."""

    __tablename__ = "balance_verification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True)
    asserted_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    derived_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    variance_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    correction_txn_id: Mapped[int | None] = mapped_column(
        ForeignKey("transaction.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())

    account: Mapped[Account] = relationship()
    correction: Mapped[Transaction | None] = relationship()


class SubscriptionStatus(enum.StrEnum):
    active = "active"
    cancelled = "cancelled"


class Subscription(TimestampMixin, Base):
    """One recurring subscription in the register (issue #107).

    Renewal dates advance per frequency; cancelled entries stay with
    full price history but emit no forecast rows."""

    __tablename__ = "subscription"
    __table_args__ = (
        CheckConstraint("expected_pence >= 0",
                        name="ck_subscription_expected"),
        CheckConstraint("tolerance_pct BETWEEN 0 AND 100",
                        name="ck_subscription_tolerance"),
        CheckConstraint("anchor_day BETWEEN 1 AND 31",
                        name="ck_subscription_anchor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    payee: Mapped[str] = mapped_column(String(200), nullable=False)
    payee_normalised: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account.id"), nullable=True)
    frequency: Mapped[Frequency] = mapped_column(
        Enum(Frequency), nullable=False)
    expected_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    tolerance_pct: Mapped[int] = mapped_column(
        Integer, default=10, nullable=False)
    next_renewal_date: Mapped[date_t] = mapped_column(Date,
                                                      nullable=False)
    anchor_day: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus), default=SubscriptionStatus.active,
        nullable=False)
    cancelled_date: Mapped[date_t | None] = mapped_column(
        Date, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="manual",
                                        nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    household: Mapped[Household] = relationship()
    prices: Mapped[list[SubscriptionPrice]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan",
        order_by="SubscriptionPrice.date")
    price_changes: Mapped[list[PriceChangeEvent]] = relationship(
        back_populates="subscription",
        order_by="PriceChangeEvent.detected_date")


class SubscriptionPrice(Base):
    """Observed price point for a subscription — price-change history."""

    __tablename__ = "subscription_price"
    __table_args__ = (
        CheckConstraint("amount_pence >= 0",
                        name="ck_subscription_price_amount"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscription.id"), nullable=False, index=True)
    date: Mapped[date_t] = mapped_column(Date, nullable=False)
    amount_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transaction.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())

    subscription: Mapped[Subscription] = relationship(
        back_populates="prices")
    transaction: Mapped[Transaction | None] = relationship()


class CandidateType(enum.StrEnum):
    subscription = "subscription"
    variable_dd = "variable_dd"


class CandidateStatus(enum.StrEnum):
    pending = "pending"
    confirmed = "confirmed"
    dismissed = "dismissed"


class DetectionCandidate(TimestampMixin, Base):
    """A recurring-outflow pattern found by the detection scan (#106).

    Nothing becomes a subscription until confirmed; dismissed rows
    suppress identical re-proposals on later scans. Variable-amount
    patterns are typed ``variable_dd`` — never confirmed as fixed
    subscriptions."""

    __tablename__ = "detection_candidate"
    __table_args__ = (
        CheckConstraint("expected_pence >= 0",
                        name="ck_candidate_expected"),
        CheckConstraint("confidence BETWEEN 0 AND 100",
                        name="ck_candidate_confidence"),
        CheckConstraint("occurrence_count >= 1",
                        name="ck_candidate_occurrences"),
        CheckConstraint(
            "status IN ('pending','confirmed','dismissed')",
            name="ck_candidate_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    payee: Mapped[str] = mapped_column(String(200), nullable=False)
    payee_normalised: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account.id"), nullable=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True)
    frequency: Mapped[Frequency] = mapped_column(
        Enum(Frequency), nullable=False)
    expected_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    tolerance_pct: Mapped[int] = mapped_column(
        Integer, default=10, nullable=False)
    candidate_type: Mapped[CandidateType] = mapped_column(
        Enum(CandidateType), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer,
                                                  nullable=False)
    first_seen: Mapped[date_t] = mapped_column(Date, nullable=False)
    last_seen: Mapped[date_t] = mapped_column(Date, nullable=False)
    status: Mapped[CandidateStatus] = mapped_column(
        Enum(CandidateStatus), default=CandidateStatus.pending,
        nullable=False)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscription.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    household: Mapped[Household] = relationship()
    subscription: Mapped[Subscription | None] = relationship()


class PriceChangeEvent(Base):
    """One detected price move on a subscription (issue #108).

    Emitted when a matching charge lands outside the subscription's
    tolerance — exactly once per change (the constraint stops double
    firing if the same day's charge is re-imported). Variable-amount
    direct debits never produce these: they cannot be confirmed into
    the register."""

    __tablename__ = "price_change_event"
    __table_args__ = (
        UniqueConstraint("subscription_id", "detected_date",
                         name="uq_pricechange_once"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscription.id"), nullable=False, index=True)
    detected_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    old_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    new_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())

    subscription: Mapped[Subscription] = relationship(
        back_populates="price_changes")

    @property
    def delta_pence(self) -> int:
        return self.new_pence - self.old_pence


class ScenarioOpKind(enum.StrEnum):
    """How a scenario op mutates the movement stream (issue #76)."""
    add = "add"          # synthetic movement on (date, amount, account)
    remove = "remove"    # suppress a real expected movement
    shift = "shift"      # move a real expected movement to a new date


class Scenario(TimestampMixin, Base):
    """A saved what-if layer (issue #76). Ops live here, never on real
    ExpectedMovement rows — sandbox data is strictly separate."""

    __tablename__ = "scenario"
    __table_args__ = (
        UniqueConstraint("household_id", "name",
                         name="uq_scenario_household_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    ops: Mapped[list[ScenarioOp]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan")


class ScenarioOp(Base):
    """One scenario operation (issue #76). For ``add`` the synthetic
    movement fields carry the payload; for ``remove``/``shift`` the
    ``expected_movement_id`` references the real movement (which is
    itself never modified)."""

    __tablename__ = "scenario_op"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("scenario.id", ondelete="CASCADE"),
        nullable=False, index=True)
    kind: Mapped[ScenarioOpKind] = mapped_column(
        Enum(ScenarioOpKind), nullable=False)
    expected_movement_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account.id"), nullable=True)
    expected_date: Mapped[date_t | None] = mapped_column(
        Date, nullable=True)
    amount_pence: Mapped[int | None] = mapped_column(
        Integer, nullable=True)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())

    scenario: Mapped[Scenario] = relationship(back_populates="ops")


class MonthlyAggregate(TimestampMixin, Base):
    """Materialised per-month analytics (issue #111). Rebuildable
    identically from raw transactions + loans — never edited by hand."""

    __tablename__ = "monthly_aggregate"
    __table_args__ = (
        UniqueConstraint("household_id", "month",
                         name="uq_monthlyagg_household_month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    month: Mapped[date_t] = mapped_column(Date, nullable=False)
    income_pence: Mapped[int] = mapped_column(Integer, default=0,
                                              nullable=False)
    expense_pence: Mapped[int] = mapped_column(Integer, default=0,
                                               nullable=False)
    savings_pence: Mapped[int] = mapped_column(Integer, default=0,
                                               nullable=False)
    net_worth_pence: Mapped[int] = mapped_column(Integer, default=0,
                                                 nullable=False)


class NotificationKind(enum.StrEnum):
    """Trigger kinds the rules engine evaluates (issue #116)."""
    spend_above = "spend_above"      # a transaction over threshold_pence
    balance_below = "balance_below"  # account balance under threshold
    # issue #117 — scheduled/date-driven kinds
    weekly_digest = "weekly_digest"    # params: {"weekday": 0..6}
    payday_reminder = "payday_reminder"  # params: {"days_before": n}
    bill_reminder = "bill_reminder"    # params: {"days_before": n}
    # issue #118 — condition alerts (severity + optional all-clear)
    low_projected_balance = "low_projected_balance"
    budget_overspend = "budget_overspend"
    unusual_transaction = "unusual_transaction"
    price_change = "price_change"
    missed_transfer = "missed_transfer"
    sync_failure = "sync_failure"


class NotificationState(enum.StrEnum):
    pending = "pending"      # raised, awaiting dispatch/digest window
    delivered = "delivered"  # included in a delivery batch
    acked = "acked"          # member acknowledged
    snoozed = "snoozed"      # muted until snoozed_until


class NotificationRule(TimestampMixin, Base):
    """A notification rule (issue #116): kind + JSON params + optional
    member target + digest config. ``params`` is a JSON object per
    kind (e.g. ``{"threshold_pence": 50000, "account_id": 3}``)."""

    __tablename__ = "notification_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[NotificationKind] = mapped_column(
        Enum(NotificationKind), nullable=False)
    params: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False,
                                         default=dict)
    target_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_profile.id"), nullable=True)
    channel: Mapped[str] = mapped_column(
        String(40), nullable=False, default="ha_notify")
    digest: Mapped[bool] = mapped_column(Boolean, default=False,
                                         nullable=False)
    digest_window_minutes: Mapped[int] = mapped_column(
        Integer, default=60, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True,
                                          nullable=False)
    # issue #119: HA notify service suffix (None → broadcast via
    # notify.notify); privacy-safe strips amounts/payees/digits.
    service: Mapped[str | None] = mapped_column(
        String(80), nullable=True)
    privacy_safe: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False)

    events: Mapped[list[NotificationEvent]] = relationship(
        back_populates="rule", cascade="all, delete-orphan")


class NotificationDelivery(Base):
    """One dispatched notification — a single event for immediate
    rules, or a digest covering N events (issue #116 batching)."""

    __tablename__ = "notification_delivery"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("notification_rule.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # issue #119 transport lifecycle: pending → sent | failed
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())

    events: Mapped[list[NotificationEvent]] = relationship(
        back_populates="delivery")


class NotificationEvent(Base):
    """A raised trigger instance (issue #116). Idempotency anchor:
    ``(rule_id, dedup_key)`` is unique — re-evaluating the same
    triggering entity can never raise a second event."""

    __tablename__ = "notification_event"
    __table_args__ = (
        UniqueConstraint("rule_id", "dedup_key",
                         name="uq_notify_event_dedup"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("notification_rule.id", ondelete="CASCADE"),
        nullable=False, index=True)
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False,
                                          default=dict)
    state: Mapped[NotificationState] = mapped_column(
        Enum(NotificationState), default=NotificationState.pending,
        nullable=False)
    delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_delivery.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)

    rule: Mapped[NotificationRule] = relationship(
        back_populates="events")
    delivery: Mapped[NotificationDelivery | None] = relationship(
        back_populates="events")


class MatchType(enum.StrEnum):
    """How a categorisation rule's pattern compares to a payee."""
    contains = "contains"
    exact = "exact"
    regex = "regex"


class RuleSource(enum.StrEnum):
    manual = "manual"    # user-authored rule
    learned = "learned"  # created/strengthened by confirmed corrections


class CategorisationRule(TimestampMixin, Base):
    """payee pattern → category (issue #68). Priority-ordered
    (lower wins); ``times_confirmed`` breaks ties — confirmed
    corrections literally strengthen a rule's future suggestions.
    ``auto_apply=False`` rules only fill ``suggested_category_id`` —
    never a silent categorisation."""

    __tablename__ = "categorisation_rule"
    # No pattern-uniqueness constraint: overlapping patterns at
    # different priorities are the whole point of precedence.

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    pattern: Mapped[str] = mapped_column(String(200), nullable=False)
    match_type: Mapped[MatchType] = mapped_column(
        Enum(MatchType), default=MatchType.contains, nullable=False)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("category.id"), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100,
                                          nullable=False)
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=False,
                                             nullable=False)
    times_confirmed: Mapped[int] = mapped_column(Integer, default=0,
                                                 nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True,
                                          nullable=False)
    source: Mapped[RuleSource] = mapped_column(
        Enum(RuleSource), default=RuleSource.manual, nullable=False)


class SyncStatus(enum.StrEnum):
    running = "running"
    completed = "completed"
    failed = "failed"      # resumable — cursor holds the resume point


class SyncCursor(TimestampMixin, Base):
    """Resumable sync state per (account, source) (issue #67).
    ``last_ref``/``last_date`` mark the last committed page; a run
    that dies mid-feed resumes from here without re-ingesting."""

    __tablename__ = "sync_cursor"
    __table_args__ = (
        UniqueConstraint("account_id", "source",
                         name="uq_synccursor_account_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True)
    source: Mapped[TransactionSource] = mapped_column(
        Enum(TransactionSource), nullable=False)
    last_ref: Mapped[str | None] = mapped_column(String(128),
                                                 nullable=True)
    # issue #70 monitoring: last good run, backoff window, queued
    # manual refresh — a refresh requested during backoff waits for
    # the window instead of bypassing it (AC).
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    refresh_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False)
    last_date: Mapped[date_t | None] = mapped_column(Date,
                                                     nullable=True)
    status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus), default=SyncStatus.completed,
        nullable=False)
    error: Mapped[str | None] = mapped_column(String(500),
                                              nullable=True)


class SyncRun(Base):
    """One ingest attempt — counters + status for monitoring."""

    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True)
    source: Mapped[TransactionSource] = mapped_column(
        Enum(TransactionSource), nullable=False)
    status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus), default=SyncStatus.running, nullable=False)
    fetched: Mapped[int] = mapped_column(Integer, default=0,
                                         nullable=False)
    inserted: Mapped[int] = mapped_column(Integer, default=0,
                                          nullable=False)
    deduped: Mapped[int] = mapped_column(Integer, default=0,
                                         nullable=False)
    merge_candidates: Mapped[int] = mapped_column(Integer, default=0,
                                                  nullable=False)
    error: Mapped[str | None] = mapped_column(String(500),
                                              nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)


class MergeStatus(enum.StrEnum):
    pending = "pending"
    merged = "merged"
    dismissed = "dismissed"


class MergeCandidate(Base):
    """A fuzzy overlap: an incoming feed row matching an existing
    transaction on (account, date, amount, payee). Flagged for
    review — never auto-deleted, never double-counted (the incoming
    row is NOT inserted)."""

    __tablename__ = "merge_candidate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True)
    existing_txn_id: Mapped[int] = mapped_column(
        ForeignKey("transaction.id"), nullable=False)
    incoming: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False)
    status: Mapped[MergeStatus] = mapped_column(
        Enum(MergeStatus), default=MergeStatus.pending,
        nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)


class ReconStatus(enum.StrEnum):
    """Reconciliation lifecycle (issue #69): `matched` closes the
    loop (auto above confidence, or user-confirmed), `pending` sits
    in the review queue, `dismissed` records a deliberate non-match."""

    pending = "pending"
    matched = "matched"
    dismissed = "dismissed"


class Reconciliation(Base):
    """Links an actual transaction to the expected movement it
    settles. Expected rows are re-materialised (issue #72), so the
    expected side is a snapshot — label/date/amount + source
    provenance — not an FK. ``candidates`` holds the ranked
    alternatives shown in the review queue for pending rows."""

    __tablename__ = "reconciliation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transaction.id"), nullable=False, index=True)
    status: Mapped[ReconStatus] = mapped_column(
        Enum(ReconStatus), default=ReconStatus.pending,
        nullable=False)
    score: Mapped[int] = mapped_column(
        Integer, nullable=False)             # 0-100
    expected_source_type: Mapped[str | None] = mapped_column(
        String(40), nullable=True)
    expected_source_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True)
    expected_label: Mapped[str | None] = mapped_column(
        String(200), nullable=True)
    expected_date: Mapped[date_t | None] = mapped_column(
        Date, nullable=True)
    expected_amount_pence: Mapped[int | None] = mapped_column(
        Integer, nullable=True)
    amount_drift_pence: Mapped[int | None] = mapped_column(
        Integer, nullable=True)              # actual − expected
    date_drift_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True)              # actual − expected
    candidates: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict)  # ranked alternatives
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)


class VaultEntry(TimestampMixin, Base):
    """Encrypted credential material (issue #30). ``ref`` is the
    opaque handle the rest of the system holds — a VaultEntry's
    ciphertext is never serialised anywhere. ``key_version`` records
    which keyring generation produced ``ciphertext`` so rotation can
    re-encrypt lazily."""

    __tablename__ = "vault_entry"
    __table_args__ = (
        UniqueConstraint("household_id", "ref",
                         name="uq_vault_household_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    ref: Mapped[str] = mapped_column(String(128), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1)


class BankProvider(enum.StrEnum):
    starling = "starling"


class ConnStatus(enum.StrEnum):
    """Connection lifecycle (issue #65): pending while unvalidated,
    connected when the token verified, degraded on transient API
    failures (429/5xx — sync retries), revoked on 401/403 or user
    disconnect — terminal until re-authenticated."""

    pending = "pending"
    connected = "connected"
    degraded = "degraded"
    revoked = "revoked"


class BankConnection(TimestampMixin, Base):
    """One bank link. The token lives in the vault under
    ``vault_ref`` — never on this row, never serialised. Sync only
    runs while status is ``connected`` or ``degraded``."""

    __tablename__ = "bank_connection"
    __table_args__ = (
        UniqueConstraint("household_id", "provider",
                         name="uq_bankconn_household_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    provider: Mapped[BankProvider] = mapped_column(
        Enum(BankProvider), nullable=False)
    vault_ref: Mapped[str] = mapped_column(
        String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(
        String(200), nullable=True)
    status: Mapped[ConnStatus] = mapped_column(
        Enum(ConnStatus), default=ConnStatus.pending,
        nullable=False)
    scopes: Mapped[str] = mapped_column(
        String(400), nullable=False, default="")  # verified at auth
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(
        String(500), nullable=True)


class MappingStatus(enum.StrEnum):
    """Discovery lifecycle (issue #66): proposed until the user
    links it to an internal account, ignored if deliberately
    skipped, closed when the provider stops returning it."""

    proposed = "proposed"
    linked = "linked"
    ignored = "ignored"
    closed = "closed"


class BankAccountMapping(TimestampMixin, Base):
    """A provider account/space discovered for a connection. Maps
    to one internal ``Account`` when the user confirms; balance is
    a point-in-time provider read (never a ledger write)."""

    __tablename__ = "bank_account_mapping"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_uid",
                         name="uq_mapping_conn_ext"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("bank_connection.id"), nullable=False,
        index=True)
    external_uid: Mapped[str] = mapped_column(
        String(64), nullable=False)
    external_label: Mapped[str] = mapped_column(
        String(200), nullable=False)
    external_kind: Mapped[str] = mapped_column(
        String(40), nullable=False, default="account")
    external_category_uid: Mapped[str | None] = mapped_column(
        String(64), nullable=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account.id"), nullable=True)
    status: Mapped[MappingStatus] = mapped_column(
        Enum(MappingStatus), default=MappingStatus.proposed,
        nullable=False)
    balance_pence: Mapped[int | None] = mapped_column(
        Integer, nullable=True)
    balance_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)


class AnomalyTrigger(enum.StrEnum):
    """Explainable detection triggers (issue #114) — each flag
    records which rule fired and the values behind it."""

    amount_outlier = "amount_outlier"      # > k·MAD within payee
    category_outlier = "category_outlier"  # > k·MAD within category
    new_payee_large = "new_payee_large"    # unseen payee, large amt
    burst = "burst"                        # freq spike same payee


class AnomalyStatus(enum.StrEnum):
    pending = "pending"
    confirmed = "confirmed"   # user says genuinely unusual
    dismissed = "dismissed"   # user says normal — feeds tuning


class AnomalyFlag(Base):
    """A suspicious transaction queued for review. ``detail`` holds
    the trigger's computed values (median, deviation, counts) so the
    flag can explain itself — the explanation is never absent."""

    __tablename__ = "anomaly_flag"
    __table_args__ = (
        UniqueConstraint("transaction_id", "trigger",
                         name="uq_anomaly_txn_trigger"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transaction.id"), nullable=False, index=True)
    trigger: Mapped[AnomalyTrigger] = mapped_column(
        Enum(AnomalyTrigger), nullable=False)
    status: Mapped[AnomalyStatus] = mapped_column(
        Enum(AnomalyStatus), default=AnomalyStatus.pending,
        nullable=False)
    detail: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)


class AuthSession(TimestampMixin, Base):
    """Server-side standalone session (issue #29). The cookie
    carries only the opaque ``token``; role changes and
    deactivation revoke rows so sessions die with the privilege
    that created them."""

    __tablename__ = "auth_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(
        String(96), nullable=False, unique=True)
    role_at_issue: Mapped[str] = mapped_column(
        String(20), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
