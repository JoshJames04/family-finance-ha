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
  `statement_import_id` / `transfer_id` (nullable).
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

- **statement_import** — one bank-statement file import (CSV/OFX/QIF):
  account, filename, format, column mapping, bounded preview payload,
  status (`preview`|`committed`|`rolled_back`), row counts.
- `transaction.statement_import_id` links committed rows back to the
  batch for audit + rollback.

## 5. Payroll

- **employment** — user, employer, tax jurisdiction, tax code (effective
  dated), NI category, student loan plan, pension scheme ref, contracted
  hours, payroll calendar (cut-off, pay date rules).
- **salary_revision** — effective date, annual salary, notes.
- **overtime_entry** — date, hours, multiplier (1.5×/2×/custom), day type
  (weekday/Saturday/Sunday/bank holiday), status (`draft`|`submitted`|
  `approved`|`rejected`), employment and snapshotted hourly rate. Work dates
  determine payroll attribution using the employment's existing cutoff.
  Monday–Sunday sheets are derived views using ISO week-year, not persisted
  records; all historic entries participate without a backfill. An opaque
  revision combines content, status and the mutation timestamp for atomic
  weekly confirmation. Existing timestamp fields use microseconds for new
  entries and strictly advance on overtime mutations; no migration is needed.
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

### Budget page KPIs (issue #415)

Four distinct measures; labels must never conflate them:

- **Planned spending** — sum of approved (effective) expense lines for
  the period; never changes because money was spent.
- **Spent so far** — external economic spending through today within the
  effective window, including categories outside the manual plan. Void rows,
  internal transfers and opening stock are excluded; splits retain their
  individual economic meaning.
- **Left to spend** — planned − spent; a negative result is reported
  as an overspend of that magnitude, never a bare negative.
- **Bills remaining** — commitment/subscription expected movements in
  the window not yet matched by a real transaction (`expected_match`);
  past-due unmatched rows count (overdue, not gone). Member budgets
  are scoped to the owner's accounts.
- **Income received** — all external receipts through today within the effective
  window, including income outside the manual plan. Received details reconcile
  to this figure; opening stock and outstanding salary forecasts are separate.
- **Shared funding** — verified net personal contributions into shared accounts,
  with other external income shown separately. All excludes internal contributions
  and calculates its calendar totals from the authorized account union.

Personal monthly plans retain nominal stored dates and whole approved amounts,
but their Budget view uses adjusted payday boundaries, including historical
plans. Shared, All and legacy calendar API clients retain calendar windows.
Per-member settings select an employment source or a fixed monthly payday without
creating salary records. Opening funds retain every scoped nonvoid ledger leg
before the effective period and expose their as-of date.

Expected movements retain nullable transfer endpoint snapshots before a scheduling
rule changes accounts or is deleted. Confirmed portions remain internal in
Budget and reports; legacy deleted rules without endpoints retain unattributed
funding. Transfer rule IDs cannot reuse retained historical identities. Endpoint
changes with matched future movements return 409 before mutation, so one partly
fulfilled occurrence cannot acquire a different counterparty or direction.

## 9. Integrity rules (enforced in domain + DB constraints)

- Sum of transfer legs per transfer = 0 across accounts.
- `actual_amount` on a locked budget line is read-only snapshot.
- Corrections never modify the original row — they supersede it.
- Deleting an account with transactions requires transfer/merge workflow.
