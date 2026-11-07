"""ORM models — platform and core financial schema (Epic 3 slice 1).

Money is stored as integer minor units (pence) per ADR-0003. The domain
layer's Money type is the only legal currency representation in logic and
at API boundaries.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    settled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
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
    transfer_id: Mapped[int | None] = mapped_column(Integer, nullable=True,
                                                    index=True)

    account: Mapped[Account] = relationship(back_populates="transactions")
    category: Mapped[Category | None] = relationship(
        back_populates="transactions")
