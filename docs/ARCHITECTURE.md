# System Architecture — Family Finance

**Status:** Proposed reference architecture — decisions tracked in
[ADRs](adr/). This document is the map; ADRs are the authority for each call.

## 1. Overview

```
┌──────────────────────── Home Assistant OS ────────────────────────┐
│                                                                   │
│  ┌────────────────── Family Finance app ──────────────────┐       │
│  │                                                        │       │
│  │   React + TypeScript SPA  ──►  FastAPI backend         │       │
│  │        (via HA Ingress)            │                   │       │
│  │                                    ▼                   │       │
│  │   ┌───────────┬───────────┬───────────┬────────────┐   │       │
│  │   │ Budgeting │ Payroll   │ Forecast  │ Import     │   │       │
│  │   │ engine    │ engine    │ engine    │ pipeline   │   │       │
│  │   └───────────┴───────────┴───────────┴────────────┘   │       │
│  │                        │              │                │       │
  │                 ┌────────▼───┐   ┌──────▼───────┐        │       │
│  │                 │ SQLite     │   │ Background   │        │       │
│  │                 │ (volume)   │   │ jobs         │        │       │
│  │                 └────────────┘   └──────────────┘        │       │
│  └────────────────────────────────────────────────────────┘       │
│                                                                   │
│  ┌── Family Finance custom integration ──┐   ┌─ HA Core ────────┐ │
│  │ sensors / services / diagnostics      │◄──│ Energy dashboard │ │
│  └───────────────┬───────────────────────┘   └──────────────────┘ │
└──────────────────┼────────────────────────────────────────────────┘
                   │ local HTTP/WebSocket API
        ┌──────────▼──────────┐
        │ Starling Bank API   │ (read-only, outbound only)
        └─────────────────────┘
```

## 2. Module boundaries

The backend is a modular monolith — clear internal boundaries now,
extractable later if ever needed.

| Module | Responsibility |
|---|---|
| `api` | FastAPI routers, request/response schemas, validation, authz middleware |
| `domain.money` | Money type, GBP arithmetic, rounding, formatting |
| `domain.accounts` | Accounts, transactions, transfers, categories, reconciliation |
| `domain.budgets` | Periods, original/revised/actual, variance, locking |
| `domain.payroll` | Salary history, overtime, UK tax engine, payslips |
| `domain.forecasting` | Schedules → dated cash-flow, safe-to-spend, scenarios |
| `domain.goals` | Savings goals, sinking funds, loans, repayments |
| `domain.seasonal` | Christmas/gifts and future seasonal modules |
| `domain.subscriptions` | Recurring detection, renewals, price changes |
| `integrations.starling` | Token vault, sync, matching (isolated adapter) |
| `integrations.ha_energy` | Tariffs, consumption → cost estimation |
| `import.statement` | Statement file → staging → validated import → reconciliation |
| `jobs` | Scheduler, sync jobs, notification jobs, maintenance |
| `notifications` | Rules engine → HA notify targets |
| `persistence` | SQLAlchemy/SQLModel models, Alembic migrations |
| `audit` | Append-only audit trail, provenance |

The frontend mirrors these domains as feature modules under a shared design
system.

## 3. Key decisions (see ADRs)

- **ADR-0001** Stack confirmed: React+TS / FastAPI / SQLite, with conditions.
- **ADR-0002** Delivery: HAOS app (add-on model) primary; HA Container
  secondary via published image — same app, different packaging.
- **ADR-0003** Money: integer minor units (pence) internally; `Decimal` at
  boundaries; GBP only; `dd/MM/yyyy`.
- **ADR-0004** Transfers are linked double-leg movements — never income or
  expense. Light double-entry semantics without full accounting UX.
- **ADR-0005** AuthN via Home Assistant, AuthZ at application level with
  household-scoped RBAC.
- **ADR-0006** Migration provenance: staged, read-only, idempotent,
  reconciled; imported data is tagged, typed and never silently corrected.
- **ADR-0007** Budget triple-value model: original / revised / actual +
  variance + accuracy, with locking.
- **ADR-0008** Starling: read-only least-privilege; token storage encrypted;
  app fully functional without it; no payment initiation, ever, without a
  new explicit requirement.

## 4. SQLite suitability assessment (ADR-0001 condition)

SQLite is sufficient **provided**:

- WAL mode + `busy_timeout` for concurrent reads during writes.
- Single-writer discipline via the backend (no direct DB access) —
  enforced *temporally* too: WAL still serialises writers, so unit
  boundaries commit promptly. Sync ingests commit per page and no
  write transaction may span provider or HA network I/O (issue #460
  — a flush-held-across-HTTP writer stalled every other writer).
- All blocking work stays off the uvicorn event loop: the scheduler
  tick runs in a worker thread, and per-request identity resolution
  (which can write — profile provisioning/display-name refresh)
  runs via `asyncio.to_thread` (#460 — an event-loop write blocked
  by a held writer froze every in-flight request → ingress 503s).
- Scheduled online backups (`VACUUM INTO` / `.backup`) — never file copies of
  a live DB.
- Alembic migrations tested upgrade **and** downgrade paths in CI.
- DB size stays modest (years of household transactions ≈ tens of MB).
- If write contention or corruption risk ever materialises, the persistence
  layer is isolated enough to swap to PostgreSQL.

HA entity history is **never** the financial store — integration publishes
read-model sensors only.

## 5. Background jobs

- Starling sync (scheduled + manual), notification rules, recurring-schedule
  materialisation, backup snapshots, retention/aggregation jobs.
- Jobs are idempotent, resumable and observable (last-run status surfaced in
  diagnostics and HA sensors where relevant).

## 6. API design

- REST under `/api/v1`; OpenAPI-generated client for the frontend.
- Versioned; breaking changes bump the version — the HA integration pins a
  supported version range.
- Problem-detail error responses; all mutations audited.

## 7. Security overview

See [SECURITY.md](SECURITY.md). In short: HA-authenticated ingress →
application-level identity → household-scoped RBAC → audit. Credentials in an
encrypted vault; never in the frontend, logs or exports.

## 8. Deployment models

| Model | Support |
|---|---|
| HAOS app (add-on + ingress) | Primary — see [HA-DEPLOYMENT](HA-DEPLOYMENT.md) |
| HA Container / standalone Docker | Secondary — same image, mounted volume, no ingress (direct port + own auth) |

## 9. Observability

- Structured JSON logs, health/readiness endpoints, diagnostics bundle for
  issue reports (sanitised), job status, sync status, migration reports.

## 10. Risks and trade-offs

| Risk | Mitigation |
|---|---|
| Starling API personal-access limitations | Spike early in M5; manual-first design keeps app viable |
| Single-developer bandwidth | Modular monolith, strong tests, phased roadmap |
| Excel data quality worse than expected | Reconciliation report + flagged-review workflow rather than silent fixes |
| HA upgrades breaking integration | Compatibility test matrix; pin HA version range; semver'd API |
| Scope creep | Roadmap phases; P3 features never gate v1.0 core |

### Historical provider category refresh

Incremental Starling sync only enriches old transactions when their references
are returned by the provider. An administrator can explicitly replay an older
window with `POST /api/v1/banking/connections/{id}/sync?historical_since=YYYY-MM-DD`.
The date cannot be in the future. Existing backoff still applies. Exact feed
references deduplicate replayed rows, update provider metadata when supplied and
retain the normal fresh watermark for subsequent incremental syncs. A provider
that does not return historical category metadata leaves it missing; the app does
not guess a category or claim that a map alone repaired history. This operation
is explicit and is not run automatically against existing installations.

Discovery commits pending changes before provider requests. Balance refresh
fetches every linked balance before applying reconciliation writes, so scheduled
and manual refreshes do not hold the SQLite writer during provider HTTP calls.
Partial occurrence payments retain their current bill/renewal head until the
remaining amount is paid. Active bill/subscription clashes are checked on create,
rename and bill reactivation as well as initial setup.

An explicit historical refresh during provider backoff returns HTTP 409 before
provider work starts. It is not queued because the incremental queue does not
persist a historical boundary; retry with the same `historical_since` after the
reported retry time. Feed sync persists its running cursor state before releasing
the writer so ORM refreshes of expired cursor fields cannot autoflush writes
immediately before the real provider adapter makes HTTP requests.

Unexpected provider parsing or ingest errors roll back the incomplete page,
preserve committed checkpoints, and persist a failed run/cursor and completion
event before propagating the error. Subscription settlement records the sum of
matched amounts; an oversized final transaction contributes only its linked
portion to the observed price.

### Employment forecast lifecycle

Employment paydays use salary and tax-code revisions effective on each payday,
clipped to the employment start/end dates. Estimates apply one period of tax
allowance, current NI/student-loan/pension settings and monthly (or weekly)
regular salary. They do not reuse a prior run's cumulative tax, overtime,
bonuses, leave or manual adjustments. Missing salary/tax/rule prerequisites
leave dates unestimated; gross-only employment does not need a tax code.
Piecework income requires logged work and generated runs. Salaried nightly rules do
not produce estimates: annual salary has no supported nightly calculation basis.

Generated payroll runs own their exact dates and supersede unmatched employment
estimates. Recorded actual net takes precedence, including zero actuals and
reopened runs; correction runs retain zero actual totals. Salary revision
create/edit/delete, tax-code creation, employment edits/replacement/deletion
refresh future estimates. Matched history and explicit salary dismissals survive
refreshes. Employment forecasts are repaired on startup and once per calendar
day by the scheduler to keep the year-long horizon rolling; this transaction
commits before any provider network request. No tax codes or future tax rules
are guessed for existing installations. Payday reminders and scenario payday
comparisons include employment projections as well as generated runs.

Employment rebuilds preserve the movement ID for the same employment, payday
and destination account, so saved scenario remove/shift operations survive
startup and daily horizon repair. Only obsolete unmatched, undismissed rows
are deleted. A changed date or destination clears scenario references to the
removed estimate; the saved operation becomes a no-op and cannot silently act
on an unrelated row if SQLite later reuses its ID.

Run supersession only adopts estimates and prior runs on the same destination
account; matched and dismissed rows on a previous destination retain their
ownership and scenario references.

### Dashboard data recovery

Monthly report figures and subscription totals have independent loading and error
boundaries. Budget spending is rendered only after actuals succeed; retrying a
budget widget retries its metadata and actuals together. An empty budget list
settles without waiting for a detail request that cannot run. Failed refreshes,
including previously empty lists, retain a visible error and Retry action.

Transaction pagination appends only a newly completed response for its requested
page and disables Load more during refresh. A recovered dashboard profile
replaces the temporary local layout and restores member save permissions.

Safe to spend has an explicit account selector; its balance and payday belong to
that account. A missing payday prompts review of salary settings for that account,
including salary and tax-code prerequisites, rather than implying all household
employment is unconfigured. The forecast widget also names its account. Budget
expected movements request the selected budget ID so the server applies its
owner scope even when two budgets cover the same dates.

Active and future salaried employer details identify missing effective salary and
PAYE tax-code revisions, payday rules and active paid-into accounts. Dated tax
codes can be added from the employer card; salary and account/payday repair reuse
the existing editors. Gross-only employment does not require PAYE tax setup.
Piecework explains that recorded shifts/runs supply income rather than invented
future shifts. Failed prerequisite reads offer Retry instead of missing-setup claims.

A failed transaction page must be retried before pagination can advance. Retained
safe-to-spend and account balances display their refresh errors beside the last
known values, and payroll-run failures expose Retry instead of claiming no runs.


### Budget and reporting acceptance boundaries

`BudgetActuals` separates full-period per-line analysis from as-of expense flow.
Expense lines include negative effective legs and net explicitly linked refunds;
unrelated positive receipts never offset them. Income lines include positive
nonrefund legs. Variance drills carry `budget_side` to select the same records.
The headline includes non-void external outflows through the earlier of today and
the period end. Explicitly linked refunds offset expense flow; all other positive
entries are receipts, regardless of category, plan membership or account scope.
An unrelated debit in the same category cannot turn a receipt into a refund.
Linked transfers and effective category legs explicitly classified as
`Internal Transfers` do not contribute to economic flows, even before pairing.
Mixed splits retain their external fee legs. The ordinary `Transfer` category
remains external. This rule also applies to reports, monthly flow analytics and
spending forecasts; account balances and net worth retain all ledger legs.
Named report income, monthly income and income-percentage rules exclude linked
refunds. Expense reports, monthly spending and category forecasts net explicit
refunds against outflows without offsetting unrelated receipts. Anomaly detection
continues to examine gross external outflows, retaining mixed-split fee amounts.
Category anomaly comparisons use the current category's effective subtotal;
payee comparisons retain the full external transaction subtotal. Uncategorised
report drills send `uncategorised=true` so they cannot expand to all expenses.
Monthly series derive fresh values from authorised raw ledger and loan records
without writing on GET or depending on a manual cache rebuild; the explicit
rebuild endpoint remains available for materialised-table compatibility.
Report internal outflow totals sum negative internal effective legs, including
confirmed unpaired movements. Internal movement counts include each paired
transfer ID once and each unpaired internal transaction once; their drill covers
both directions, while the internal outflow drill selects negative legs.
Opening transactions (`system`, `open:<account-id>`) remain balance stock and are
excluded from period flow. The returned itemised bridge uses effective split
amounts and preserves uncategorised status without writing classifications.

Shared Budget actuals, unpaid obligations and budget-scoped expected movements
use shared accounts (owner NULL); personal Budgets use the owner's accounts.
Accountless household obligations remain shared. The read-only combined Budget
endpoint selects one latest visible plan per owner
for the exact period, adds their plan lines, and computes actuals and unpaid bill
residuals over a union of authorised accounts. Shared and personal overlap cannot
multiply a transaction or expected obligation. Alternative plan versions for the
same owner/period do not multiply the plan. An owner view retains normal editing.
All includes every authorised account even when an account's owner has no plan
in the selected period; plan lines sum only the plans that actually exist.

Monthly, annual and comparative reports apply the caller's account and budget
visibility to both current and prior sections. Personal budget variances reuse the
Budget actuals calculation, including the owner scope and signed category refunds.
Shared Budget variance drills include `shared_only=true`; personal drills carry
the owner filter. Transaction drills retain non-void/opening/transfer exclusions. Account
balances and net worth continue to include opening stock.

Expected movements retain their original scheduled amounts and expose
`outstanding_pence` from contribution matching plus `dismissed_at`. Budget salary
forecasts use the undismissed residual of employment/payroll-run movements within
the selected period and account scope. Forecast salary remains separate from
manual plan totals and received ledger income; fully matched salary adds no
remaining forecast income.

Account projections and scenario baselines use that same matching status for
every expected movement, retaining its sign and flooring the remaining magnitude
at zero. Already received salary and already paid liabilities remain in ledger
balance only. Next payday and scenario payday comparison dates exclude satisfied
and dismissed salary movements. Scenario edits operate on the remaining amount;
their payday anchors still come from the baseline. These reads preserve stored
amounts, match rows, account visibility and the existing future-date bounds.

Deleting a budget template removes its copied-line source and clears optional
provenance pointers; previously applied budget lines are retained. Tariff deletion
removes the selected effective-dated rate and leaves readings intact. Both DELETE
routes are household scoped, audited, and reject viewer mutations.
Bank categorisation policies are applied after each ingest. Explicit rules may be
restricted by `account_id` and signed `amount_pence`; leave `auto_apply` false for
suggestions. A confirmed correction continues to teach suggestions only. An
exact, account-scoped rule may declare `transfer_account_id` to identify an owned
account alias without changing the bank counterparty name. Alias targets must be
a different account in the same household. Use an ordinary category rule for
person-to-person transfers; do not declare that counterparty as an owned account.
Merchant names shared by subscriptions and small purchases need separate amount
and account scopes. There is no global Apple amount rule.

Owned account-name movements receive Internal Transfers only when uncategorised.
Automatic pairing additionally requires unique reciprocal account names or
explicit reciprocal owned-account aliases, equal opposite amounts, the same date
and currency, and different accounts. Ambiguous or unrelated equal amounts stay
unpaired. Existing authored categories are retained. Historical links are not
silently rewritten by this migration.

Rules that include an account or owned destination are visible only when both
accounts are visible to the caller. Member-triggered apply, provider-map reapply,
transfer pairing and refund linking operate on that caller's authorized accounts.
Learned associations retain their originating account; old unscoped learned
payees with only inaccessible transaction history are withheld from other users.
Trusted scheduler passes continue to process the household internally.

Linked provider account labels are additional owned aliases only when the source
and destination mappings share both the connection and provider account UID, and
the label resolves uniquely. Proposed mappings, other connections, other account
UIDs and ambiguous labels confer no alias. Human names are never inferred from
UIDs; absent aliases require an explicit account-scoped rule before deployment.

Canonical HA family events use compact `app_meta` prior-state JSON and outbox rows
in the same mutation transaction. The scheduled evaluator compares per-budget
signed ledger flow, per-event gift totals, per-period energy estimates, and actual
payslip lifecycle states; it runs independently of notification-rule configuration.
First observation seeds existing financial state quietly. Expected payday lead
windows use employment/date occurrence deduplication, not summary count movement.
Financial and snapshot rollback is atomic. Shared automation/report energy budgets
and shared budget event flow exclude private plans/accounts before aggregation.
Automatic categorisation, suggestions, provider mapping, owned movement
classification, transfer pairing and refund-credit linking skip reconciled rows
and rows inside locked periods. A refund can still reference a reconciled
purchase because the purchase is unchanged. Canonical occurrence settlement
inherits its source category, then applies provider/rule fallbacks to that one
editable row, before reconciliation freezes it. Existing user categories and
protected historical rows remain unchanged.

## Personal pay-cycle Budget views

Budget records retain their nominal plan dates and lifecycle. Single-budget
detail/actuals accept `basis=pay_cycle`; the default remains calendar-based for
existing dashboard/report consumers. The personal Budget page opts in, while
Shared and All remain calendar views. A returned period object carries effective
dates, anchor employment and configuration warnings. All downstream actuals,
bills and expected-movement queries use that effective range.

`budget_periods` reuses payroll payday parsing and adjustment rules. Each monthly
boundary independently selects the employment applicable on that adjusted
payday, preserving continuity through job changes. Explicit per-member anchors
are validated and stored under scoped `budget_pay_cycle:<household>:<member>`
AppMeta keys. Ambiguous, stale or missing monthly sources produce a visible
calendar fallback. Weekly/nightly jobs cannot silently repeat a whole monthly
plan. Existing holiday-table coverage and payday policy remain authoritative.
Members can instead select a fixed monthly payday independently of employment;
this uses scoped `budget_pay_cycle_day:<household>:<member>` AppMeta state and
does not create a job or salary forecast. Manual and employment selections clear
one another, and Automatic clears both. Explicit employment selection applies
within that employment's date coverage; unique known former jobs retain their
historical payday rules.

`budget_funding` itemizes effective external receipts, including categories absent
from the plan. Verified personal/shared boundaries and confirmed contribution
matches provide signed shared contributions; shared-space movements do not.
Unidentified movement and inaccessible source attribution remain explicit.
Manual matches consume only same-direction effective split legs; confirmed
internal portions use the shared `confirmed_transfers` economic classifier across
Budget actuals, monthly reports, analytics and forecast history. Its SQL window
consumes already-internal legs first; unmatched external fees/receipts stay
economic flow. Funding attribution requests raw legs to explain the same confirmed
portions without changing ledger records. Forecast residuals remain
separate from actual income, preventing double counting after matching.

Opening funds are balance stock across authorized scoped accounts, using every
nonvoid ledger leg before the period. Opening transactions are counted once;
account opening metadata is not added again. Future windows cap this snapshot
at today and expose `opening_as_of`. The stock is never included in received
income or silently added to approved spending. GETs derive all views without
rewriting historical plans, statuses or ledger data. Migration 0070 adds nullable
endpoint snapshots to expected movements. Deleting a scheduling rule preserves
its confirmed contribution history through those snapshots. Account edits freeze
the original endpoints before changing a rule. Endpoint changes return 409 when
future movements already have matches: a separate rule must represent the new
accounts, avoiding reinterpretation of a partly fulfilled obligation. Older deleted rules
without endpoints still provide internal-transfer evidence; their funding stays
unattributed rather than guessing a contributor.

## Weekly overtime sheets

The Overtime page derives Monday–Sunday groups from each entry's work date for
the selected employment. ISO week numbers use the ISO week-year, including
cross-year/week-53 history. The week header shows dates, hours, snapshotted pay
and status counts. There is no separate persisted week state or history rewrite.

Weekly submit processes drafts; weekly approve/reject processes submitted entries.
Other entries remain unchanged, and all individual actions remain available.
A confirmation captures the complete eligible set of IDs and opaque revisions.
Revisions include content, status and mutation timestamps, so edited drafts and
status cycles cannot silently reuse an earlier confirmation. Creation timestamps
retain microseconds to distinguish same-second deleted/recreated IDs; mutating
an entry strictly advances its timestamp. POST/PATCH refresh the persisted
representation before returning a revision, including legacy accepted precision.

The employment-scoped weekly endpoint uses the existing authorization boundary.
It reserves the SQLite writer before reading the eligible set, preserving every
employment field while preventing membership/content interleaving. Existing
individual mutating reads share that reservation. Conditional transitions and
per-entry audit events with week bounds share one transaction/savepoint. Stale
membership, revisions, repeat requests and concurrent changes return a refreshable
409 with no partial transitions or audit events. An API/input failure remains an
error; the UI does not show it as an empty week or silently retry a stale claim.

Grouping and status transitions preserve work dates, rate/multiplier snapshots,
payroll-cutoff attribution and already-generated payroll components. Approved-only
payroll behaviour is unchanged, including weeks crossing a cutoff or year. This
workflow records submission/approval in the app; it does not send manager emails.
