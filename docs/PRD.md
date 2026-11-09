# Product Requirements — Family Finance

**Status:** Active development specification — requirements remain subject to the acceptance gates in GitHub Issues
**Owner:** JoshJames04
**Audience:** Maintainer(s), future contributors

## 1. Vision

Family Finance is a self-hosted household finance application, hosted inside
Home Assistant with a polished, responsive UI in the HA sidebar. It replaces
the `Family Budget.xlsx` workbook used since 2024 and becomes the single
source of truth for household income, expenditure, forecasting, salaries,
transfers, savings and family financial commitments.

Excel is a **one-time historical migration source only**. Once migration is
validated and reconciled, the workbook is retired — no synchronisation, no
ongoing reference.

The product should be comparable in capability to Actual Budget, without
copying its UI or being constrained to envelope budgeting.

## 2. Goals / success criteria

- All workbook functionality (except discontinued "Piecework Co") is recreated
  and improved.
- Migration produces a reconciled, auditable dataset; the Excel file can be
  safely archived after the end-to-end acceptance scenario passes.
- Daily-driver usability: reviewing transactions, updating forecasts and
  recording overtime take seconds, on desktop and mobile.
- Data survives years of use: versioning, migrations, backups, restore and
  rollback are designed-in and tested.
- The app remains fully functional with manually managed accounts; banking
  integration is additive, never required.

## 3. Non-goals / out of scope

- Bank payment initiation or any money movement via bank APIs (read-only).
- Envelope/zero-based budgeting as a mandated methodology.
- Multi-household / multi-tenant SaaS.
- Investment tracking, crypto, tax filing (v1.0).
- "Piecework Co" functionality (discontinued) — historical transactions retained
  for reporting only.

## 4. Users and access model

| Persona | Needs |
|---|---|
| Household admin (primary user) | Full control: accounts, budgets, payroll, integrations, member management |
| Household member | Shared finances plus their own private budgets/salary |
| Read-only viewer | Dashboards/reports without edit rights |

Shared household data vs private personal data is a first-class model concept,
enforced at API level — not via dashboard visibility.

## 5. Functional scope (epics)

| # | Epic | Phase |
|---|---|---|
| 1 | Application Foundation & HA Hosting | M1 |
| 2 | Authentication, Profiles & Security | M1–M2 |
| 3 | Financial Database & Accounts | M2 |
| 4 | Excel Migration & Historical Data Integrity | M2 |
| 5 | Budget Management | M3 |
| 6 | Banking & Starling Integration | M5 |
| 7 | Cash-Flow Forecasting & Payday Intelligence | M5 |
| 8 | Salary, Overtime & UK Payroll | M4 |
| 9 | Household Contributions & Internal Transfers | M4 |
| 10 | Savings, Goals, Loans & Repayments | M4 |
| 11 | Christmas & Gift Budgeting | M4 |
| 12 | HA Integration & Energy Costs | M5 |
| 13 | Subscriptions & Recurring Expenses | M6 |
| 14 | Analytics, Forecasting & Reporting | M6 |
| 15 | Notifications & Automation | M6 |
| 16 | UI/UX & Accessibility | M3→ |
| 17 | Testing, Reliability, Backups & Release | M7 (continuous) |

Detailed requirements per epic live in the epic issues themselves.

## 6. Excel migration requirements

Source: `Family Budget.xlsx` (~12 sheets, 7 hidden) covering 2024–2027 budgets,
overtime, salary history, payroll, tax tables, family loan tracking, Christmas
budgeting, a second member's budget and discontinued Piecework Co data.

Principles:

- Never modify the source workbook; import is read-only.
- Repeatable, idempotent, dry-run capable, rollback supported.
- Full provenance: source sheet/cell references and import timestamps on
  every imported record.
- Imported values distinguished from verified bank transactions and from
  original forecasts (overwritten forecasts are classified, never assumed).
- Aggregates stored distinctly from transaction-level data; overlap with
  later bank imports must not double-count.
- Detailed reconciliation report; unresolved discrepancies flagged for
  manual review, never silently corrected.

### Known workbook defects (each has a dedicated issue)

| ID | Defect | Handling |
|---|---|---|
| A | 2026/2027 annual house-bill totals include intermediate monthly sums, overstating expenditure | Recompute independently from records; never reproduce the formula |
| B | Family repayment `#REF!` breaks remaining-balance calculation | Reconstruct from payment history; flag discrepancies |
| C | Historical annual totals ≠ sum of monthly values | Reconciliation checks + report |
| D | PAYE/NI hardcoded instead of reference tables | Replaced by versioned, configurable tax engine |
| E | Forecast cells overwritten by actuals | Classify imported values correctly; never treat as original forecast |
| F | Payroll periods outside model range → zero/incorrect values | Validate period boundaries and opening balances |

## 7. Non-functional requirements

- **Money:** decimal-exact GBP arithmetic (minor units / `Decimal`), no
  float errors; UK locale, `dd/MM/yyyy`.
- **Integrity:** transfers never counted as income/expense; audit trail on
  all financial mutations; period locking preserves approved budgets.
- **Security:** app-level authz, encrypted credential storage, least-privilege
  scopes, audit trails, no credentials in frontend.
- **Reliability:** deterministic calculations, tested backup/restore,
  upgrade and rollback paths that cannot corrupt history.
- **Performance:** responsive on modest home hardware (HAOS-class devices).
- **Portability:** all user data exportable in open formats.
- **Accessibility:** WCAG-conscious UI, keyboard usable, clear states.

## 8. Open questions / clarifications

- Workbook not yet available in-repo (by design); secure inspection is the
  first migration spike and may refine import scope.
- Starling personal access token scopes/rate limits to be confirmed in the
  banking spike.
- Household composition (number of profiles) assumed 2 initially; model must
  not hardcode this.
- Payroll specifics (pension scheme type, student loan plan, tax code) to be
  captured as employment configuration during Epic 8.
