"""ORM models — platform and core financial schema (Epic 3 slice 1).

Money is stored as integer minor units (pence) per ADR-0003. The domain
layer's Money type is the only legal currency representation in logic and
at API boundaries.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from datetime import date as date_t

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
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
        back_populates="category")


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

    account: Mapped[Account] = relationship(back_populates="transactions")
    category: Mapped[Category | None] = relationship(
        back_populates="transactions")
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

    budget: Mapped[Budget] = relationship(back_populates="lines")

    @property
    def effective_pence(self) -> int:
        return (self.revised_pence if self.revised_pence is not None
                else self.original_pence)
