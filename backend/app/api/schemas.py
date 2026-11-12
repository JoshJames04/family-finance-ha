"""API schemas — Money at every boundary (floats rejected by Money)."""

from __future__ import annotations

import json
from datetime import date, datetime
from datetime import date as date_t
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)

from app.domain import appconfig
from app.domain.money import Money
from app.persistence.models import (
    AccountType,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    CandidateStatus,
    CandidateType,
    Confidence,
    DayType,
    ExpectedSource,
    Frequency,
    GoalType,
    LoanDirection,
    OvertimeStatus,
    PaymentStatus,
    PayrollComponentKind,
    PayrollRunStatus,
    PensionMode,
    PurchaseStatus,
    Role,
    StudentLoanPlan,
    SubscriptionStatus,
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
    min_balance_pence: int | None = None
    owner_profile_id: int | None = None  # private↔shared (audited)


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
    min_balance_pence: int | None


class ProjectionDayOut(BaseModel):
    date: date
    movements_pence: int
    balance_pence: int


class ProjectionOut(BaseModel):
    account_id: int
    start_balance_pence: int
    next_payday: date | None
    committed_until_payday_pence: int
    safe_to_spend_pence: int
    min_balance_pence: int
    min_balance_date: date | None
    threshold_pence: int | None
    below_threshold: bool
    days: list[ProjectionDayOut]


class WarningSourceOut(BaseModel):
    account_id: int
    name: str
    available_pence: int


class OverdraftWarningOut(BaseModel):
    account_id: int
    name: str
    first_dip_date: date
    floor_pence: int
    min_balance_pence: int
    deficit_pence: int
    suggested_sources: list[WarningSourceOut]


class BalanceVerifyIn(BaseModel):
    asserted_balance: Money
    verified_date: date
    correct: bool = False
    note: str | None = Field(default=None, max_length=500)


class BalanceVerificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_id: int
    asserted_pence: int
    derived_pence: int
    variance_pence: int
    verified_date: date
    correction_txn_id: int | None
    note: str | None


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


class TransactionCorrect(BaseModel):
    """Correction request for a reconciled transaction (issue #124):
    the reason is mandatory — corrections are audit events, not edits."""
    reason: str = Field(min_length=1, max_length=500)
    payee: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: int | None = None
    notes: str | None = Field(default=None, max_length=2000)


class TransactionSplitIn(BaseModel):
    category_id: int
    amount_pence: int


class TransactionSplitsPut(BaseModel):
    """Whole-set replace for a transaction's splits (issue #289)."""
    splits: list[TransactionSplitIn] = Field(min_length=0,
                                           max_length=50)


class TransactionSplitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    transaction_id: int
    category_id: int
    amount_pence: int
    position: int


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
    suggested_category_id: int | None
    corrects_id: int | None
    splits: list[TransactionSplitOut] = []


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


class CommitmentPatch(BaseModel):
    """Editable fields of a recurring commitment. Schedule-affecting
    changes regenerate the commitment's future expected rows."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    account_id: int | None = None
    category_id: int | None = None
    amount: Money | None = None
    formula: str | None = Field(default=None, max_length=200)
    frequency: Frequency | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    next_date: date | None = None
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


class BudgetSpendItem(BaseModel):
    transaction_id: int
    date: date
    category_id: int | None
    amount_pence: int  # signed spend: refunds are negative


class BudgetActuals(BaseModel):
    budget_id: int
    as_of: date | None = None
    spent_pence: int = 0
    budget_covered_pence: int = 0
    unallocated_pence: int = 0
    uncategorised_pence: int = 0
    income_received_pence: int = 0
    spend_items: list[BudgetSpendItem] = []
    totals: dict[int, int]  # line_id → actual pence
    lines: dict[int, dict[str, object]] = {}  # variance/accuracy per line
    # issue #415: unpaid scheduled outgoings left in the period —
    # distinct from `totals` (real spend) and from planned amounts
    bills_remaining_pence: int = 0
    bills_remaining_count: int = 0


class BudgetCombined(BaseModel):
    detail: BudgetDetail
    actuals: BudgetActuals


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
    ha_user_id: str | None = None


class DashboardLayoutIn(BaseModel):
    """Per-member dashboard arrangement (issue #123): widget order +
    hidden set. Keys are client-side registry ids — the API stores the
    arrangement opaquely and bounds its size."""
    order: list[str] = Field(default_factory=list, max_length=50)
    hidden: list[str] = Field(default_factory=list, max_length=50)


class DashboardOut(BaseModel):
    role: Role | None
    layout: DashboardLayoutIn | None


class ProfilePatch(BaseModel):
    role: Role | None = None
    display_name: str | None = Field(
        default=None, min_length=1, max_length=120)
    ha_user_id: str | None = Field(default=None, max_length=64)


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


class AccuracyRankRow(BaseModel):
    category_id: int
    name: str
    periods: int
    mean_accuracy_pct: float
    mean_original_accuracy_pct: float
    revision_delta_pct: float


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
    taken_at: datetime | None = None


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
    paid_into_account_id: int | None = None
    overtime_multipliers: dict[DayType, int] | None = None
    pension_mode: PensionMode = PensionMode.none
    pension_employee_bps: int = Field(default=0, ge=0, le=10_000)
    pension_fixed_pence: int = Field(default=0, ge=0)
    employment_type: Literal["salaried", "piecework"] = "salaried"
    deduction_mode: Literal["paye", "gross_only"] = "paye"
    initial_salary: Money | None = None
    initial_salary_effective_date: date | None = None
    initial_tax_code: str | None = Field(default=None, max_length=12)
    replaces_employment_id: int | None = None
    replacement_end_date: date | None = None
    replacement_confirmed: bool = False
    start_date: date | None = None
    work_days_per_week: Decimal = Field(default=Decimal("5"), gt=0,
                                        le=7)


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
    paid_into_account_id: int | None = None
    overtime_multipliers: dict[DayType, int] | None = None
    pension_mode: PensionMode | None = None
    pension_employee_bps: int | None = Field(default=None, ge=0,
                                            le=10_000)
    pension_fixed_pence: int | None = Field(default=None, ge=0)
    work_days_per_week: Decimal | None = Field(default=None, gt=0,
                                               le=7)
    is_active: bool | None = None
    end_date: date | None = None
    employment_type: Literal["salaried", "piecework"] | None = None
    deduction_mode: Literal["paye", "gross_only"] | None = None

    @model_validator(mode="after")
    def ordered_dates(self) -> EmploymentPatch:
        if self.start_date is not None and self.end_date is not None \
                and self.end_date < self.start_date:
            raise ValueError("employment end date cannot precede start date")
        return self
    start_date: date | None = None


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
    paid_into_account_id: int | None
    pension_mode: PensionMode
    pension_employee_bps: int
    pension_fixed_pence: int
    work_days_per_week: Decimal
    is_active: bool
    end_date: date_t | None
    start_date: date_t | None
    employment_type: str
    deduction_mode: str


class DeliveryChargeIn(BaseModel):
    amount: Money
    label: str | None = Field(default=None, max_length=200)


class DeliveryChargeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    delivery_entry_id: int
    amount_pence: int
    label: str | None


class DeliveryIn(BaseModel):
    work_date: date
    hours: Decimal = Field(default=Decimal("0"), ge=0, le=24)
    hourly_rate: Money | None = None
    deliveries: int = Field(default=0, ge=0)
    delivery_rate: Money | None = None
    tips: Money | None = None
    charges: list[DeliveryChargeIn] = Field(default_factory=list,
                                             max_length=200)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("hourly_rate", "delivery_rate", "tips")
    @classmethod
    def nonnegative_money(cls, value: Money | None) -> Money | None:
        if value is not None and value.pence < 0:
            raise ValueError("piecework rates and tips cannot be negative")
        return value

    @model_validator(mode="after")
    def require_rate_for_hours(self) -> DeliveryIn:
        if self.hours > 0 and self.hourly_rate is None:
            raise ValueError("hourly rate is required when hours are logged")
        return self


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    employment_id: int
    work_date: date
    hours: Decimal
    hourly_rate_pence: int
    deliveries: int
    delivery_rate_pence: int
    tips_pence: int
    source_transaction_id: int | None
    imported_total_pence: int | None
    needs_review: bool
    notes: str | None
    charges: list[DeliveryChargeOut] = []
    pay_pence: int


class PrepareYearIn(BaseModel):
    year: int = Field(ge=2000, le=2200)


class PrepareYearOut(BaseModel):
    year: int
    runs_created: int
    runs_skipped: int
    budgets_created: int
    warnings: list[str]


class SalaryRevisionIn(BaseModel):
    effective_date: date
    annual_salary: Money
    notes: str | None = Field(default=None, max_length=2000)


class SalaryRevisionPatch(BaseModel):
    effective_date: date | None = None
    annual_salary: Money | None = None
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


class OvertimePatch(BaseModel):
    work_date: date | None = None
    hours: Decimal | None = Field(default=None, gt=0, le=24)
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
    unpaid_leave_days: Decimal | None = Field(default=None, gt=0,
                                              le=31)


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
    rule_set_year: str | None
    rule_set_version: int
    gross_pence: int
    net_pence: int
    corrects_id: int | None
    components: list[PayrollComponentOut] = []

    @computed_field
    def actual_gross_pence(self) -> int | None:
        return self._totals()[0]

    @computed_field
    def actual_net_pence(self) -> int | None:
        return self._totals()[1]

    def _totals(self) -> tuple[int | None, int | None]:
        """Authoritative run totals + signed per-component actual deltas —
        mirrors domain.payroll_totals for the serialized shape."""
        if self.status is not PayrollRunStatus.reconciled:
            return None, None
        earnings = {"base_salary", "overtime", "bonus", "allowance"}
        gross, net = self.gross_pence, self.net_pence
        for c in self.components:
            if c.actual_pence is None:
                continue
            signed_actual = (c.actual_pence if c.kind.value in earnings
                             else -abs(c.actual_pence))
            signed_forecast = (c.forecast_pence if c.kind.value in earnings
                               else -abs(c.forecast_pence))
            net += signed_actual - signed_forecast
            if c.kind.value in earnings:
                gross += c.actual_pence - c.forecast_pence
        return gross, net


class PayslipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    payroll_run_id: int
    original_name: str
    size_bytes: int
    media_type: str
    status: str
    extracted: dict[str, object] | None = None
    warnings: list[str] = []
    overtime_findings: list[dict[str, object]] = []


class PayslipReviewPatch(BaseModel):
    components: dict[str, int] | None = None
    warnings: list[str] | None = None
    overtime_lines: list[PayslipOvertimeLineIn] | None = None


class PayslipOvertimeLineIn(BaseModel):
    id: str
    hours: Decimal = Field(gt=0)
    printed_rate_pence: int = Field(ge=0)


class PayslipConfirmIn(BaseModel):
    actuals: dict[PayrollComponentKind, int] = Field(default_factory=dict)


class OvertimeFindingResolveIn(BaseModel):
    action: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=1000)
    work_date: date | None = None
    day_type: DayType | None = None
    hourly_rate_pence: int | None = Field(default=None, ge=0)
    multiplier_bps: int | None = Field(default=None, gt=0)
    snooze_until: datetime | None = None


class ExpectedMovementIn(BaseModel):
    account_id: int | None = None
    expected_date: date
    amount: Money
    label: str = Field(min_length=1, max_length=200)


class ExpectedMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    household_id: int
    account_id: int | None
    expected_date: date_t
    amount_pence: int
    label: str
    confidence: Confidence
    source_type: ExpectedSource
    source_id: int


class FixedSpecIn(BaseModel):
    kind: Literal["fixed"]
    pence: int = Field(gt=0)


class IncomePctSpecIn(BaseModel):
    kind: Literal["income_pct"]
    bps: int = Field(gt=0, le=10000)


class BudgetLineSpecIn(BaseModel):
    kind: Literal["budget_line"]
    category_id: int


AmountSpecIn = Annotated[
    FixedSpecIn | IncomePctSpecIn | BudgetLineSpecIn,
    Field(discriminator="kind")]


class TransferRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_account_id: int
    dest_account_id: int
    amount_spec: AmountSpecIn
    frequency: Frequency
    day_of_month: int = Field(ge=1, le=31)
    start_date: date
    end_date: date | None = None
    notes: str | None = Field(default=None, max_length=500)


class TransferRulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_account_id: int | None = None
    dest_account_id: int | None = None
    amount_spec: AmountSpecIn | None = None
    frequency: Frequency | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = Field(default=None, max_length=500)
    active: bool | None = None


class TransferRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    source_account_id: int
    dest_account_id: int
    amount_spec: dict[str, Any]
    frequency: Frequency
    day_of_month: int
    start_date: date
    end_date: date | None
    active: bool
    notes: str | None

    @field_validator("amount_spec", mode="before")
    @classmethod
    def _parse_spec(cls, v: Any) -> Any:
        if isinstance(v, str):
            return json.loads(v)
        return v


class EvaluateResult(BaseModel):
    amount_pence: int | None
    confidence: Confidence


class PayrollOverrideIn(BaseModel):
    """Run-scoped manual adjustment: signed delta on a component kind."""
    kind: PayrollComponentKind
    delta: Money
    reason: str = Field(min_length=1, max_length=300)


class PayrollOverrideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    run_id: int
    kind: PayrollComponentKind
    delta_pence: int
    reason: str
    actor: str


class ContributionMatchIn(BaseModel):
    transaction_id: int
    amount: Money | None = None


class ContributionStatusOut(BaseModel):
    movement_id: int
    expected_pence: int
    matched_pence: int
    outstanding_pence: int
    state: str


class MatchResult(BaseModel):
    matched: int


class OutstandingItemOut(BaseModel):
    movement_id: int
    account_id: int | None
    label: str
    due_date: date
    age_days: int
    outstanding_pence: int


class RequirementOut(BaseModel):
    account_id: int
    due_date: date
    scheduled_pence: int
    satisfied_pence: int
    outstanding_pence: int


class MemberContributionOut(BaseModel):
    profile_id: int
    display_name: str
    contributed_pence: int


class RescheduleIn(BaseModel):
    due_date: date


class SavingsGoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal_type: GoalType
    target: Money
    target_date: date | None = None
    start_date: date
    account_id: int
    notes: str | None = Field(default=None, max_length=500)


class SavingsGoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    goal_type: GoalType
    target_pence: int
    target_date: date | None
    start_date: date
    account_id: int
    active: bool
    notes: str | None


class GoalProgressOut(BaseModel):
    contributed_pence: int
    remaining_pence: int
    monthly_need_pence: int | None
    pct: int
    average_rate_pence: int
    projected_date: date | None
    on_track: bool | None


class GoalContributionOut(BaseModel):
    transaction_id: int
    date: date
    amount_pence: int
    payee: str


class LoanIn(BaseModel):
    party: str = Field(min_length=1, max_length=120)
    direction: LoanDirection
    principal: Money
    start_date: date
    expected_payment: Money | None = None
    interest_bps_pa: int | None = Field(default=None, ge=0)
    owner_profile_id: int | None = None  # private↔shared
    notes: str | None = Field(default=None, max_length=500)


class LoanPatch(BaseModel):
    party: str | None = Field(default=None, min_length=1,
                              max_length=120)
    direction: LoanDirection | None = None
    principal_pence: int | None = None
    start_date: date | None = None
    expected_payment_pence: int | None = None
    interest_bps_pa: int | None = Field(default=None, ge=0)
    owner_profile_id: int | None = None  # private↔shared (audited)
    active: bool | None = None
    notes: str | None = Field(default=None, max_length=500)


class LoanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_profile_id: int | None
    party: str
    direction: LoanDirection
    principal_pence: int
    start_date: date
    expected_payment_pence: int | None
    interest_bps_pa: int | None
    active: bool
    notes: str | None


class RepaymentIn(BaseModel):
    date: date
    amount: Money
    transaction_id: int | None = None
    notes: str | None = Field(default=None, max_length=300)


class RepaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    loan_id: int
    date: date
    amount_pence: int
    transaction_id: int | None
    notes: str | None
    balance_after_pence: int | None = None


class LoanPositionOut(BaseModel):
    outstanding_pence: int
    repaid_pence: int
    accrued_interest_pence: int
    expected_to_date_pence: int
    drift_pence: int


class PayoffPoint(BaseModel):
    months: int | None
    payoff_date: date | None


class LoanProjectionOut(BaseModel):
    baseline: PayoffPoint
    with_extra: PayoffPoint
    extra_monthly_pence: int
    months_saved: int | None


class SeasonalEventIn(BaseModel):
    owner_profile_id: int | None = None
    year: int = Field(ge=1900, le=2200)
    type: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    budget: Money
    savings_goal_id: int | None = None
    notes: str | None = Field(default=None, max_length=500)


class SeasonalEventOut(BaseModel):
    owner_profile_id: int | None = None
    model_config = ConfigDict(from_attributes=True)
    id: int
    year: int
    type: str
    name: str
    budget_pence: int
    savings_goal_id: int | None
    notes: str | None


class EventTotalsOut(BaseModel):
    remaining_after_commitments_pence: int
    planned_pence: int
    actual_pence: int
    committed_pence: int
    remaining_pence: int


class RecipientComparisonOut(BaseModel):
    recipient: str
    earlier_actual_pence: int
    later_actual_pence: int
    delta_pence: int
    earlier_gifts: int
    later_gifts: int


class EventComparisonOut(BaseModel):
    """Year-on-year comparison of two seasonal events (issue #99)."""
    earlier: EventTotalsOut
    later: EventTotalsOut
    actual_delta_pence: int
    earlier_avg_gift_pence: int | None
    later_avg_gift_pence: int | None
    recipients: list[RecipientComparisonOut]


class GiftIn(BaseModel):
    recipient: str = Field(min_length=1, max_length=120)
    idea: str = Field(min_length=1, max_length=300)
    planned: Money
    actual: Money | None = None
    notes: str | None = Field(default=None, max_length=300)


class GiftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_id: int
    recipient: str
    idea: str
    planned_pence: int
    actual_pence: int | None
    purchase_status: PurchaseStatus
    payment_status: PaymentStatus
    transaction_id: int | None
    notes: str | None


class GiftTransitionIn(BaseModel):
    to: PurchaseStatus


class GiftPayIn(BaseModel):
    actual: Money | None = None
    transaction_id: int | None = None


class EventRolloverIn(BaseModel):
    year: int = Field(ge=1900, le=2200)
    copy_planned: bool = False
    carry_remaining: bool = False


class SubscriptionIn(BaseModel):
    payee: str = Field(min_length=1, max_length=200)
    frequency: Frequency
    expected: Money
    next_renewal_date: date
    category_id: int | None = None
    account_id: int | None = None
    tolerance_pct: int = Field(default=10, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=500)


class SubscriptionPatch(BaseModel):
    payee: str | None = Field(default=None, min_length=1,
                              max_length=200)
    frequency: Frequency | None = None
    expected: Money | None = None
    next_renewal_date: date | None = None
    category_id: int | None = None
    account_id: int | None = None
    tolerance_pct: int | None = Field(default=None, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=500)


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    payee: str
    frequency: Frequency
    expected_pence: int
    tolerance_pct: int
    next_renewal_date: date
    status: SubscriptionStatus
    cancelled_date: date | None
    category_id: int | None
    account_id: int | None
    source: str
    notes: str | None
    overdue: bool = False


class SubscriptionCancelIn(BaseModel):
    date: date


class SubscriptionPaymentIn(BaseModel):
    date: date
    amount: Money
    transaction_id: int | None = None


class SubscriptionPriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    subscription_id: int
    date: date
    amount_pence: int
    transaction_id: int | None


class DetectionScanIn(BaseModel):
    min_occurrences: int = Field(default=3, ge=2, le=12)


class DetectionCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    payee: str
    frequency: Frequency
    expected_pence: int
    tolerance_pct: int
    candidate_type: CandidateType
    confidence: int
    occurrence_count: int
    first_seen: date
    last_seen: date
    status: CandidateStatus
    subscription_id: int | None
    category_id: int | None
    account_id: int | None
    notes: str | None


class CandidateConfirmIn(BaseModel):
    category_id: int | None = None
    account_id: int | None = None
    tolerance_pct: int | None = Field(default=None, ge=0, le=100)


class CandidateDismissIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class PriceChangeEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    subscription_id: int
    detected_date: date
    old_pence: int
    new_pence: int
    delta_pence: int


class AnnualisedSubOut(BaseModel):
    subscription_id: int
    payee: str
    frequency: Frequency
    expected_pence: int
    annualised_pence: int


class AnnualisedOut(BaseModel):
    per_subscription: list[AnnualisedSubOut]
    total_annualised_pence: int


class ReportDrillOut(BaseModel):
    """Endpoint + params that reproduce a figure's record set."""
    endpoint: str
    params: dict[str, Any]


class ReportFigureOut(BaseModel):
    key: str
    label: str
    amount_pence: int | None
    prior_amount_pence: int | None = None
    delta_pence: int | None = None
    count: int | None = None
    drill: ReportDrillOut | None = None
    components: list[ReportDrillOut] | None = None


class ReportSectionOut(BaseModel):
    key: str
    label: str
    figures: list[ReportFigureOut]


class ReportPeriodOut(BaseModel):
    start: date
    end: date


class ReportOut(BaseModel):
    period: ReportPeriodOut
    prior_period: ReportPeriodOut | None = None
    sections: list[ReportSectionOut]
class MonthlyAggregateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    month: date
    income_pence: int
    expense_pence: int
    savings_pence: int
    net_worth_pence: int


class NetWorthOut(BaseModel):
    net_worth_pence: int



class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ScenarioOpIn(BaseModel):
    kind: str = Field(pattern="^(add|remove|shift)$")
    expected_movement_id: int | None = None
    account_id: int | None = None
    expected_date: date | None = None
    amount: Money | None = None
    label: str | None = Field(default=None, max_length=200)


class ScenarioOpOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    scenario_id: int
    kind: str
    expected_movement_id: int | None
    account_id: int | None
    expected_date: date | None
    amount_pence: int | None
    label: str | None


class ScenarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    household_id: int
    name: str


class PaydayDeltaOut(BaseModel):
    date: date
    baseline_balance_pence: int
    scenario_balance_pence: int
    delta_pence: int


class ScenarioDiffOut(BaseModel):
    min_balance_changed: bool
    baseline_min_balance_pence: int
    baseline_min_balance_date: date | None
    scenario_min_balance_pence: int
    scenario_min_balance_date: date | None
    min_balance_delta_pence: int
    safe_to_spend_delta_pence: int
    paydays: list[PaydayDeltaOut]


class ScenarioRunOut(BaseModel):
    scenario_id: int
    account_id: int
    days: int
    baseline: ProjectionOut
    projection: ProjectionOut
    diff: ScenarioDiffOut


class ForecastRowOut(BaseModel):
    category_id: int | None
    month: date
    amount_pence: int
    method: str
    confidence: str
    input_start: date | None
    input_end: date | None
    input_months: int


class NotificationRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(pattern="^(spend_above|balance_below|payroll_variance)$")
    params: dict[str, object] = Field(default_factory=dict)
    target_profile_id: int | None = None
    channel: str = Field(default="ha_notify", max_length=40)
    digest: bool = False
    digest_window_minutes: int = Field(default=60, ge=1, le=10080)
    service: str | None = Field(default=None, max_length=80)
    privacy_safe: bool = True


class NotificationRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    household_id: int
    name: str
    kind: str
    params: dict[str, object]
    target_profile_id: int | None
    channel: str
    digest: bool
    digest_window_minutes: int
    enabled: bool
    service: str | None
    privacy_safe: bool


class NotificationEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rule_id: int
    dedup_key: str
    payload: dict[str, object]
    state: str
    delivery_id: int | None
    created_at: datetime
    delivered_at: datetime | None
    snoozed_until: datetime | None


class NotificationDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rule_id: int
    channel: str
    event_count: int
    status: str
    error: str | None
    sent_at: datetime | None
    created_at: datetime


class DispatchOut(BaseModel):
    deliveries: list[NotificationDeliveryOut]
    events_delivered: int


class DeliverOut(BaseModel):
    sent: int
    failed: int


class CategorisationRuleIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    match_type: str = Field(default="contains",
                            pattern="^(contains|exact|regex)$")
    category_id: int
    account_id: int | None = None
    transfer_account_id: int | None = None
    amount_pence: int | None = None
    priority: int = 100
    auto_apply: bool = False


class CategorisationRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    household_id: int
    pattern: str
    match_type: str
    category_id: int
    account_id: int | None = None
    transfer_account_id: int | None = None
    amount_pence: int | None = None
    priority: int
    auto_apply: bool
    times_confirmed: int
    enabled: bool
    source: str


class CategoriseApplyOut(BaseModel):
    auto_applied: int
    suggested: int
    unmatched: int


class PairRefundOut(BaseModel):
    linked: int


class ProviderCategoryMapIn(BaseModel):
    provider: str = Field(default="starling", min_length=1,
                          max_length=20)
    provider_category: str = Field(min_length=1, max_length=64)
    category_id: int


class ProviderCategoryMapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider: str
    provider_category: str
    category_id: int


class ObservedProviderCategoryOut(BaseModel):
    provider: str
    provider_category: str
    transaction_count: int
    mapped_category_id: int | None


class PayeeOut(BaseModel):
    payee: str
    transaction_count: int


class PayeeRenameIn(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)


class PayeeRenameOut(BaseModel):
    renamed: int
    skipped: int


class SyncCursorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_id: int
    source: str
    last_ref: str | None
    last_date: date | None
    status: str
    error: str | None
    # issue #70: last-good + backoff/queue visibility
    last_success_at: datetime | None = None
    retry_at: datetime | None = None
    refresh_requested: bool = False


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_id: int
    source: str
    status: str
    fetched: int
    inserted: int
    deduped: int
    merge_candidates: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class MergeCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_id: int
    existing_txn_id: int
    incoming: dict[str, object]
    status: str
    created_at: datetime
    resolved_at: datetime | None


class ConnectionSyncStatusOut(BaseModel):
    """Per-linked-mapping bank sync state for the Settings panel
    (issue #431). Timestamps only — names, refs and amounts never
    cross this boundary."""
    connection_id: int
    provider: str
    status: str
    last_error: str | None
    account_id: int | None
    last_attempted_at: datetime | None
    last_success_at: datetime | None
    refresh_queued: bool
    retry_at: datetime | None
    next_due_at: datetime | None


class BankingSyncSettingsOut(BaseModel):
    """Effective bank-sync cadence + where it comes from
    (``environment`` | ``app`` | ``default``) and per-connection
    status (issue #431)."""
    interval_minutes: float
    source: str
    editable: bool
    min_minutes: float
    max_minutes: float
    tick_seconds: float
    connections: list[ConnectionSyncStatusOut]


class BankingSyncSettingsPatch(BaseModel):
    sync_interval_minutes: float = Field(
        ge=appconfig.SYNC_INTERVAL_MIN_MINUTES,
        le=appconfig.SYNC_INTERVAL_MAX_MINUTES)


class MergeResolveIn(BaseModel):
    action: str = Field(pattern="^(merged|dismissed)$")


class ReconciliationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_id: int
    transaction_id: int
    status: str
    score: int
    expected_source_type: str | None
    expected_source_id: int | None
    expected_label: str | None
    expected_date: date | None
    expected_amount_pence: int | None
    amount_drift_pence: int | None
    date_drift_days: int | None
    candidates: dict[str, object]
    created_at: datetime
    resolved_at: datetime | None


class ReconStatsOut(BaseModel):
    scanned: int
    matched: int
    queued: int


class CredentialRefOut(BaseModel):
    """The only credential shape an API response may carry — the
    opaque handle. There is deliberately no field capable of
    holding secret material (issue #30 schema-level guarantee)."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    ref: str
    key_version: int
    created_at: datetime
    updated_at: datetime


class CredentialStoreIn(BaseModel):
    """Write-only input: the secret enters here and is never
    serialised back out. ``SecretStr`` also keeps it out of reprs
    and validation-error echoes."""
    ref: str = Field(min_length=1, max_length=128)
    secret: SecretStr


class BankConnectionOut(BaseModel):
    """Never carries token material — the token lives in the vault
    and is write-only (issue #65 schema-level guarantee)."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider: str
    label: str | None
    status: str
    scopes: str
    last_validated_at: datetime | None
    last_error: str | None


class BankConnectIn(BaseModel):
    provider: str = Field(pattern="^starling$")
    token: SecretStr
    label: str | None = Field(default=None, max_length=200)

    @field_validator("token")
    @classmethod
    def _header_safe_token(cls, v: SecretStr) -> SecretStr:
        try:
            v.get_secret_value().encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValueError(
                "token contains characters not encodable for HTTP "
                "headers") from exc
        return v


class BankMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    connection_id: int
    external_uid: str
    external_label: str
    external_kind: str
    account_id: int | None
    status: str
    balance_pence: int | None
    balance_at: datetime | None


class MappingLinkIn(BaseModel):
    account_id: int | None = None
    create_account: bool = False   # mint an internal Account if set
    account_type: str | None = None  # "personal" | "joint" on mint


class AnomalyFlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_id: int
    transaction_id: int
    trigger: str
    status: str
    detail: dict[str, object]
    created_at: datetime
    resolved_at: datetime | None


class AnomalyScanOut(BaseModel):
    scanned: int
    flagged: int
    suppressed: int


class LoginIn(BaseModel):
    """Standalone-mode credential pair (issue #29). ``username``
    matches a profile's display name in the household."""

    username: str
    password: str


class EnergyTariffIn(BaseModel):
    """Effective-dated tariff (issue #103). ``unit_rate_millipence`` is
    pence×1000 per kWh — UK quotes like 24.501p/kWh stay exact."""
    effective_from: date_t
    unit_rate_millipence: int = Field(ge=0)
    standing_charge: Money


class EnergyTariffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    effective_from: date_t
    unit_rate_millipence: int
    standing_charge_pence: int


class EnergyReadingIn(BaseModel):
    """One day's metered consumption in whole watt-hours."""
    date: date_t
    wh: int = Field(ge=0)
    source: str = Field(default="manual", max_length=30)


class EnergyReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    date: date_t
    wh: int
    source: str


class EnergyDayOut(BaseModel):
    date: date_t
    wh: int
    unit_pence: int
    standing_pence: int
    tariff_id: int


class EnergyEstimateOut(BaseModel):
    """Estimated (never billed) cost over an inclusive window — the
    ``estimate`` tag keeps it distinct from supplier-bill transactions."""
    kind: Literal["estimate"] = "estimate"
    start: date_t
    end: date_t
    days: list[EnergyDayOut]
    unit_pence: int
    standing_pence: int
    total_pence: int
    missing_days: int


class EnergyConfigIn(BaseModel):
    meter_entity: str | None = Field(default=None, max_length=200)
    budget_category_id: int | None = None


class EnergyConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    meter_entity: str | None
    budget_category_id: int | None


class EnergyMonthReportOut(BaseModel):
    """Issue #104 — month-to-date vs budget + month-end projection.
    All estimate figures are estimates, not billed amounts."""
    kind: Literal["estimate"] = "estimate"
    month_start: date_t
    month_end: date_t
    through: date_t
    unit_pence: int
    standing_pence: int
    total_pence: int
    missing_days: int
    budget_pence: int | None
    variance_pence: int | None
    projected_total_pence: int | None
    projected_variance_pence: int | None
    avg_daily_wh: int | None


class StatementImportRowOut(BaseModel):
    date: date
    amount_pence: int
    payee: str
    notes: str | None
    duplicate: bool


class StatementImportOut(BaseModel):
    id: int
    account_id: int
    filename: str
    format: str
    status: str
    rows_total: int
    rows_imported: int
    rows_skipped: int
    created_at: datetime


class StatementImportPreviewOut(BaseModel):
    import_row: StatementImportOut
    rows: list[StatementImportRowOut]


class IntegrationEventOut(BaseModel):
    seq: int
    event_type: str
    entity_kind: str | None
    entity_id: int | None
    occurred_at: datetime
    data: dict[str, object]


class IntegrationFeedOut(BaseModel):
    events: list[IntegrationEventOut]
    next_cursor: int


class IntegrationItemOut(BaseModel):
    family: Literal["bills", "accounts", "goals"]
    id: str
    name: str
    value: float
    unit: str


class IntegrationItemsOut(BaseModel):
    items: list[IntegrationItemOut]


class IntegrationSettingsOut(BaseModel):
    detail: str  # "safe" | "detailed"


class IntegrationSettingsPatch(BaseModel):
    detail: str
