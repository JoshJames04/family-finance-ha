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
The headline includes non-void external outflows through the earlier of today and
the period end. Positive entries in an expense category, and explicitly linked
refunds, offset expense flow; remaining positive entries are income. An unlinked,
uncategorised inflow remains income rather than being guessed to be a refund.
Opening transactions (`system`, `open:<account-id>`) remain balance stock and are
excluded from period flow. The returned itemised bridge uses effective split
amounts and preserves uncategorised status without writing classifications.

The read-only combined Budget endpoint selects one latest visible plan per owner
for the exact period, adds their plan lines, and computes actuals and unpaid bill
residuals over a union of authorised accounts. Shared and personal overlap cannot
multiply a transaction or expected obligation. Alternative plan versions for the
same owner/period do not multiply the plan. An owner view retains normal editing.

Monthly, annual and comparative reports apply the caller's account and budget
visibility to both current and prior sections. Personal budget variances reuse the
Budget actuals calculation, including the owner scope and signed category refunds.
Transaction drills carry owner and non-void/opening/transfer exclusions. Account
balances and net worth continue to include opening stock.

Deleting a budget template removes its copied-line source and clears optional
provenance pointers; previously applied budget lines are retained. Tariff deletion
removes the selected effective-dated rate and leaves readings intact. Both DELETE
routes are household scoped, audited, and reject viewer mutations.
