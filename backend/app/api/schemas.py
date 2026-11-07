"""API schemas — Money at every boundary (floats rejected by Money)."""

from __future__ import annotations

from datetime import date
from datetime import date as date_t

from pydantic import BaseModel, ConfigDict, Field

from app.domain.money import Money
from app.persistence.models import (
    AccountType,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    Frequency,
    TransactionSource,
    TransactionStatus,
)


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    size: int


class HouseholdIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class HouseholdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: AccountType
    opening_balance: Money
    opening_date: date
    owner_profile_id: int | None = None
    institution_ref: str | None = Field(default=None, max_length=128)


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    institution_ref: str | None = Field(default=None, max_length=128)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    type: AccountType
    currency: str
    opening_balance_pence: int
    opening_date: date
    owner_profile_id: int | None
    institution_ref: str | None
    archived: bool


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: int | None = None
    is_system: bool = False


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: int | None = None
    archived: bool | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    parent_id: int | None
    name: str
    is_system: bool
    archived: bool


class TransactionIn(BaseModel):
    account_id: int
    date: date
    amount: Money
    payee: str = Field(min_length=1, max_length=200)
    category_id: int | None = None
    notes: str | None = Field(default=None, max_length=2000)


class TransactionPatch(BaseModel):
    payee: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: int | None = None
    notes: str | None = Field(default=None, max_length=2000)
    status: TransactionStatus | None = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_id: int
    date: date_t
    settled_date: date_t | None
    amount_pence: int
    payee: str
    category_id: int | None
    status: TransactionStatus
    source: TransactionSource
    notes: str | None
    external_ref: str | None
    transfer_id: int | None
    refund_of_id: int | None


class BulkTransactionOp(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    category_id: int | None = None
    status: TransactionStatus | None = None


class BulkResult(BaseModel):
    applied: list[int]
    rejected: list[dict[str, str]]


class TransferIn(BaseModel):
    household_id: int | None = None  # resolved from context when omitted
    debit_transaction_id: int
    credit_transaction_id: int
    matched: bool = True
    rule_ref: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=2000)


class TransferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    household_id: int
    matched: bool
    rule_ref: str | None
    notes: str | None
    legs: list[int]


class CommitmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    account_id: int
    category_id: int | None = None
    amount: Money | None = None
    formula: str | None = Field(default=None, max_length=200)
    frequency: Frequency
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    next_date: date
    end_date: date | None = None
    occurrence_count: int | None = Field(default=None, ge=1)
    notes: str | None = Field(default=None, max_length=2000)


class CommitmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    account_id: int
    category_id: int | None
    amount_pence: int | None
    formula: str | None
    frequency: Frequency
    day_of_month: int | None
    next_date: date
    last_generated: date | None
    end_date: date | None
    occurrence_count: int | None
    generated_count: int
    active: bool
    notes: str | None


class MaterialiseResult(BaseModel):
    created: list[int]
    next_date: date


class BudgetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    period_type: BudgetPeriodType
    start_date: date
    end_date: date
    owner_profile_id: int | None = None


class BudgetLineIn(BaseModel):
    category_id: int
    side: BudgetLineSide
    amount: Money


class BudgetLineRevise(BaseModel):
    amount: Money


class BudgetLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category_id: int
    side: BudgetLineSide
    original_pence: int
    revised_pence: int | None
    effective_pence: int


class BudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_profile_id: int | None
    name: str
    period_type: BudgetPeriodType
    start_date: date
    end_date: date
    status: BudgetStatus


class BudgetDetail(BudgetOut):
    lines: list[BudgetLineOut]
    read_only: bool


class BudgetActuals(BaseModel):
    budget_id: int
    totals: dict[int, int]  # line_id → actual pence
