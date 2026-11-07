"""API schemas — Money at every boundary (floats rejected by Money)."""

from __future__ import annotations

from datetime import date
from datetime import date as date_t
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.money import Money
from app.persistence.models import (
    AccountType,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    DayType,
    Frequency,
    OvertimeStatus,
    PayrollComponentKind,
    PayrollRunStatus,
    Role,
    StudentLoanPlan,
    TaxJurisdiction,
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
    external_ref: str | None = Field(default=None, max_length=128)
    refund_of_id: int | None = None


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
    rollover: bool = False


class BudgetLineRevise(BaseModel):
    amount: Money
    reason: str = Field(min_length=1, max_length=200)


class BudgetLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category_id: int
    side: BudgetLineSide
    original_pence: int
    revised_pence: int | None
    rollover: bool
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
    lines: dict[int, dict[str, object]] = {}  # variance/accuracy per line


class BudgetTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class BudgetTemplateLineIn(BaseModel):
    category_id: int
    side: BudgetLineSide
    amount: Money


class BudgetTemplateLinePatch(BaseModel):
    amount: Money


class BudgetTemplateLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category_id: int
    side: BudgetLineSide
    amount_pence: int


class BudgetTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class BudgetTemplateDetail(BudgetTemplateOut):
    lines: list[BudgetTemplateLineOut]


class CopyForwardIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    start_date: date
    end_date: date
    period_type: BudgetPeriodType | None = None


class CopyResult(BaseModel):
    budget: BudgetOut
    warnings: list[str]


class ApplyTemplateIn(BaseModel):
    template_id: int


class ApplyResult(BaseModel):
    created: int
    warnings: list[str]


class RolloverIn(BaseModel):
    source_budget_id: int


class RolloverResult(BaseModel):
    applied: int
    carries: list[dict[str, object]]


class ProfileIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    role: Role = Role.member
    ha_user_id: str | None = Field(default=None, max_length=64)


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_name: str
    role: Role
    is_active: bool


class ShareBudgetIn(BaseModel):
    share: bool = True


class BudgetReportLine(BaseModel):
    line_id: int
    category_id: int
    original_pence: int
    revised_pence: int | None
    revision_reason: str | None
    effective_pence: int
    actual_pence: int
    variance_pence: int
    variance_pct: float
    accuracy_pct: float
    original_accuracy_pct: float
    status: str


class BudgetReport(BaseModel):
    budget_id: int
    lines: list[BudgetReportLine]


class AccuracyPoint(BaseModel):
    budget_id: int
    name: str
    start_date: str
    end_date: str
    actual_pence: int
    effective_pence: int
    variance_pence: int
    accuracy_pct: float
    original_accuracy_pct: float


class AccuracyHistory(BaseModel):
    category_id: int
    points: list[AccuracyPoint]


class BulkLineReviseItem(BaseModel):
    line_id: int
    amount: Money
    reason: str = Field(min_length=1, max_length=200)


class BulkLineRevise(BaseModel):
    revisions: list[BulkLineReviseItem] = Field(min_length=1,
                                                max_length=500)


class BulkReviseResult(BaseModel):
    updated: int
    skipped: list[int] = []  # line ids not found in budget


class BackupOut(BaseModel):
    name: str
    bytes: int
    ok: bool


class RestoreRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class RestoreAccepted(BaseModel):
    name: str
    applies_on_next_start: bool = True


class EmploymentIn(BaseModel):
    profile_id: int
    employer: str = Field(min_length=1, max_length=200)
    jurisdiction: TaxJurisdiction = TaxJurisdiction.england_wales
    ni_category: str = Field(default="A", min_length=1, max_length=2)
    student_loan_plan: StudentLoanPlan = StudentLoanPlan.none
    pension_scheme_ref: str | None = Field(default=None, max_length=128)
    contracted_hours: Decimal = Field(gt=0, le=168)
    payday_rule: str | None = Field(default=None, max_length=400)
    overtime_multipliers: dict[DayType, int] | None = None


class EmploymentPatch(BaseModel):
    employer: str | None = Field(default=None, min_length=1,
                                 max_length=200)
    jurisdiction: TaxJurisdiction | None = None
    ni_category: str | None = Field(default=None, min_length=1,
                                    max_length=2)
    student_loan_plan: StudentLoanPlan | None = None
    pension_scheme_ref: str | None = Field(default=None,
                                           max_length=128)
    contracted_hours: Decimal | None = Field(default=None, gt=0,
                                             le=168)
    payday_rule: str | None = Field(default=None, max_length=400)
    overtime_multipliers: dict[DayType, int] | None = None
    is_active: bool | None = None


class EmploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    household_id: int
    profile_id: int
    employer: str
    jurisdiction: TaxJurisdiction
    ni_category: str
    student_loan_plan: StudentLoanPlan
    pension_scheme_ref: str | None
    contracted_hours: Decimal
    payday_rule: str | None
    is_active: bool


class SalaryRevisionIn(BaseModel):
    effective_date: date
    annual_salary: Money
    notes: str | None = Field(default=None, max_length=2000)


class TaxCodeRevisionIn(BaseModel):
    effective_date: date
    tax_code: str = Field(min_length=1, max_length=12)
    notes: str | None = Field(default=None, max_length=2000)


class SalaryRevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    employment_id: int
    effective_date: date_t
    annual_salary_pence: int
    notes: str | None


class TaxCodeRevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    employment_id: int
    effective_date: date_t
    tax_code: str
    notes: str | None


class SalaryAtOut(BaseModel):
    on: date_t
    annual_salary_pence: int
    hourly_rate_pence: int


class GrossPeriodOut(BaseModel):
    start: date_t
    end: date_t
    gross_pence: int


class OvertimeIn(BaseModel):
    work_date: date
    hours: Decimal = Field(gt=0, le=24)
    multiplier_bps: int | None = Field(default=None, gt=0, le=100_000)
    day_type: DayType | None = None
    notes: str | None = Field(default=None, max_length=2000)


class OvertimeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    employment_id: int
    work_date: date_t
    hours: Decimal
    multiplier_bps: int
    day_type: DayType
    status: OvertimeStatus
    hourly_rate_pence: int
    pay_pence: int
    notes: str | None


class PayrollRunIn(BaseModel):
    period_start: date
    period_end: date
    pay_date: date
    bonus: Money | None = None
    allowance: Money | None = None
    unpaid_leave: Money | None = None


class PayrollReconcileIn(BaseModel):
    """actual amounts keyed by component kind, e.g. {"tax": 29500}.
    Absent kinds are assumed to match forecast."""
    actuals: dict[PayrollComponentKind, int] = Field(default_factory=dict)


class PayrollComponentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: PayrollComponentKind
    label: str
    forecast_pence: int
    actual_pence: int | None
    variance_pence: int | None


class PayrollRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    employment_id: int
    period_start: date_t
    period_end: date_t
    pay_date: date_t
    status: PayrollRunStatus
    tax_year: str
    rule_set_version: int
    gross_pence: int
    net_pence: int
    corrects_id: int | None
    components: list[PayrollComponentOut] = []
