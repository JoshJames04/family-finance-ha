# Data Model — Family Finance

**Status:** Initial proposal — refined during M2 schema issues.
Conventions: `id` surrogate keys, `created_at`/`updated_at` everywhere,
monetary columns are integer minor units (pence) unless stated, dates ISO in
storage / `dd/MM/yyyy` in UI. Soft-delete only where noted; financial history
is never hard-deleted.

## 1. Identity & access

- **household** — name, settings (payday defaults, locale, retention policy).
- **user_profile** — HA user linkage, display name, role
  (`admin` | `member` | `viewer`), active flag.
- **access_scope** — per-entity visibility: `shared` | `private(owner)`.
- **audit_event** — actor, action, entity, before/after digest, timestamp.
  Append-only.

## 2. Accounts & transactions

- **account** — name, type (`personal`|`joint`|`bills`|`savings`|`cash`|
  `credit`|`loan`), owner scope, currency=GBP, opening balance + date,
  institution ref (e.g. Starling account id), archived flag.
- **category** — hierarchical (parent → subcategory), system vs user,
  archived flag. Discontinued categories (e.g. Piecework Co) stay archived for
  historical reporting.
- **transaction** — account, date (transaction date), payment/settlement
  date, amount (signed pence), payee, category, status
  (`pending`|`cleared`|`reconciled`|`void`), notes, source
  (`manual`|`starling`|`excel_import`|`system`), external ref (dedup key),
  provenance ref → `import_record`, `transfer_id` (nullable).
- **transfer** — two transaction legs (debit+credit), rule ref, matched flag.
  Transfers are excluded from income/expense by query contract.
- **recurring_commitment** — amount or formula, frequency, next/last dates,
  account, category, end conditions, source.
- **correction** — supersedes a transaction (never edits history): original
  ref, corrected values, reason, actor.
- **reporting_period** — calendar mapping, locked flag (locks budgets).

## 3. Budgets

- **budget** — scope (household|user), period ref, status
  (`draft`|`approved`|`locked`).
- **budget_line** — category, `original_amount`, `revised_amount`,
  `actual_amount` (derived/cached), notes. Original is immutable post-lock.
- **budget_template** — reusable line sets; copy-forward support.
- **budget_variance** — materialised view: original vs revised vs actual,
  accuracy metrics per period.

## 4. Import & provenance

- **import_batch** — file hash, timestamp, actor, status
  (`dry_run`|`committed`|`rolled_back`), row counts, report ref.
- **import_record** — batch, sheet, cell range, raw value, interpreted value,
  classification (`actual`|`forecast_overwritten`|`aggregate`|`unknown`),
  target entity + id, flag for review.
- **migration_flag** — defect register outcome (A–F), resolution status,
  reviewer note.

## 5. Payroll

- **employment** — user, employer, tax jurisdiction, tax code (effective
  dated), NI category, student loan plan, pension scheme ref, contracted
  hours, payroll calendar (cut-off, pay date rules).
- **salary_revision** — effective date, annual salary, notes.
- **overtime_entry** — date, hours, multiplier (1.5×/2×/custom), day type
  (weekday/Saturday/Sunday/bank holiday), status (`draft`|`submitted`|
  `approved`), payroll period ref.
- **payroll_run** — period, gross, deductions breakdown (PAYE/NI/SL/pension),
  net, forecast vs payslip actual, reconciliation state.
- **tax_rule_set** — versioned UK parameters (thresholds, bands, rates,
  allowance) keyed by tax year; never edited in place — new version per year.
- **manual_override** — run-scoped adjustment, reason, actor (never mutates
  history).

## 6. Goals, loans & seasonal

- **savings_goal** — name, target, target date, linked account/pot,
  contributions ledger ref.
- **loan** — party, principal, schedule, interest (if any), outstanding
  balance (derived), manual-verification events.
- **repayment** — loan ref, date, amount, source/provenance.
- **seasonal_event** — year, type (`christmas`, extensible), total budget,
  savings goal link.
- **gift** — event ref, recipient, idea, planned/estimated cost, actual cost,
  purchase status, payment status.

## 7. Subscriptions & integrations

- **subscription** — payee pattern, frequency, expected amount (+tolerance),
  renewal date, status, history of price points, `variable_dd` flag.
- **bank_connection** — institution, status, scopes, last sync, error state.
- **credential_ref** — pointer into encrypted vault (tokens never in tables).
- **sync_cursor** — per-account incremental position.
- **energy_tariff** — unit rates, standing charge, effective dates, meter
  entity refs.
- **energy_cost_snapshot** — period, consumption, estimated cost, budget link.

## 8. Derived/read models

Safe-to-spend, forecast positions, net worth and report outputs are
**derived** — cached tables allowed for performance but always rebuildable
from the above.

## 9. Integrity rules (enforced in domain + DB constraints)

- Sum of transfer legs per transfer = 0 across accounts.
- `actual_amount` on a locked budget line is read-only snapshot.
- Every `transaction.source='excel_import'` has an `import_record`.
- Corrections never modify the original row — they supersede it.
- Deleting an account with transactions requires transfer/merge workflow.
