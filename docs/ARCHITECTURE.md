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
| `import.excel` | Workbook reader → staging → validated import → reconciliation |
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
- Single-writer discipline via the backend (no direct DB access).
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
