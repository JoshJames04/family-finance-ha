# Family Finance

[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-ea4aaa?logo=github)](https://github.com/sponsors/JoshJames04)

A self-hosted household finance application for Home Assistant. Family Finance is
designed to become the single source of truth for a household's financial life:
budgeting, forecasting, payroll, savings, debts, subscriptions and day-to-day
spending — all in one place, under your control.

> **Status:** Released and in live household use. `develop` is the integration branch; `main` is the release branch with tagged `v1.x` releases published to GHCR. The Excel workbook was retired as the system of record on 2026-11-09 after full parity verification ([docs/MIGRATION.md](docs/MIGRATION.md)). The backlog lives in
> GitHub Issues; delivery is tracked on the
> [Family Finance project board](https://github.com/users/JoshJames04/projects/1)
> (Backlog / Board / Current Sprint / Roadmap / By Epic views) with phases in
> [Milestones](https://github.com/JoshJames04/family-finance-ha/milestones) and
> the [Roadmap](docs/ROADMAP.md).

## Why this exists

Since 2024 the household finances have been managed in a Microsoft Excel
workbook (`Family Budget.xlsx`). It works, but it is fragile: overwritten
forecasts, broken formulas, hardcoded tax assumptions and no access control.
Family Finance replaces it with a purpose-built application. The workbook is a
one-time migration source only — it is never a runtime dependency.

## What it does

The [specialist finance audit disposition](docs/FINANCE-HISTORICAL-AUDIT.md)
documents payroll cutoffs, private salary and savings boundaries, versioned
tax corrections, loan attribution, and seasonal actual versus committed budgets.

- **Budgeting** — monthly, annual and custom-period budgets with original
  budget, revised forecast, actuals, variance and forecast-accuracy tracking.
- **Cash-flow forecasting** — date-aware projected balances, safe-to-spend,
  payday intelligence and overdraft risk warnings. Salary is projected from
  the employment's payday rule — no run needed — using effective salary/tax
  configuration and regular deductions, into the account it is
  paid into. Account and scenario forecasts include only outstanding amounts
  after matching; received salary and paid bills are not projected again.
- **UK payroll** — salary history, overtime (configurable multipliers), PAYE,
  National Insurance, student loans, pension and payslip reconciliation.
  Overtime is grouped into Monday–Sunday weekly sheets, including historical
  entries, with week-level submit/approve/reject and individual entry actions.
- **Banking** — secure, read-only Starling Bank synchronisation with duplicate
  detection, automatic categorisation and reconciliation. Categories resolve
  automatically: matched bills inherit their commitment's category, the
  household-owned bank-category map auto-applies, and payee rules learn from
  every confirmed correction. Settings shows whether automatic sync is enabled
  and any retry deadline; concurrent refreshes queue safely. Balance history is
  repaired only after a complete successful feed using a fresh provider snapshot.
  The app is fully functional without it.
- **Household contributions** — configurable transfer rules between personal,
  joint and bills accounts, with outstanding-transfer detection.
  Expected payments count live eligible payment amounts and retain their matches
  through financially equivalent corrections; void entries cannot mark them paid.
- **Savings, goals and debts** — sinking funds, savings goals, family loans and
  repayment schedules.
- **Christmas and gifts** — seasonal budgeting with recipients, gift tracking
  and year-on-year comparison.
- **Subscriptions** — recurring-payment detection, renewal tracking and
  price-change alerts.
- **Home Assistant** — runs as a Home Assistant app with sidebar ingress
  access, plus a custom integration exposing financial sensors, services and
  energy-cost awareness.
- **Reporting** — monthly/annual reports, trends, net worth, explainable
  forecasts and data export.
- **Dashboard customisation** — widget order and visibility persist per member.
  Failed saves remain clearly marked as unsaved, with Retry and Revert; drafts
  survive navigation and reload in the same browser tab.

## Architecture at a glance

| Component | Technology |
|---|---|
| Frontend | React + TypeScript |
| Backend | Python + FastAPI |
| Database | SQLite (persistent volume, WAL mode) |
| Hosting | Home Assistant OS app (ingress) |
| HA integration | Custom integration (sensors + services) |
| Banking | Starling Bank API (read-only) |
| CI/CD | GitHub Actions |

Full rationale and trade-offs are documented in
[Architecture](docs/ARCHITECTURE.md) and the [ADRs](docs/adr/).

## Documentation

- [Product Requirements](docs/PRD.md)
- [System Architecture](docs/ARCHITECTURE.md)
- [Data Model](docs/DATA-MODEL.md)
- [Home Assistant Deployment](docs/HA-DEPLOYMENT.md)
- [Excel Migration Strategy](docs/MIGRATION.md)
- [Security Model](docs/SECURITY.md)
- [Release Process](docs/RELEASE.md)
- [Roadmap](docs/ROADMAP.md)
- [Contributing & Standards](CONTRIBUTING.md)
- [Architecture Decision Records](docs/adr/)

## Development

All work flows through GitHub Issues: epics → user stories/tasks → pull
requests against `develop`; `main` is release-only. See
[CONTRIBUTING](CONTRIBUTING.md) for branching, Definition of Ready/Done,
labels and review standards.

### Quickstart (dev environment)

```bash
# Boot backend + frontend + stubbed HA API (requires Docker)
python scripts/dev.py up          # or: docker compose up --build -d
python scripts/dev.py seed        # load synthetic fixture data
# frontend → http://localhost:5173   backend → http://localhost:8099/docs

# Backend tests (local venv)
cd backend && pip install -e '.[dev]' && pytest
```

Layout: `backend/` (FastAPI), `frontend/` (Vite + React + TS), `addon/` (HA
app packaging), `custom_components/` (HA integration), `dev/` (HA API stub),
`tests/` (shared synthetic fixtures).

An ingress-faithful stack for regression work lives in
`docker-compose.ingress.yml`: the production image behind a
Supervisor-emulating proxy at the trusted ingress IP with chaos
fault injection (`dev/ingress-proxy/`), plus the issue #460
acceptance harness `dev/ff460_acceptance.py` (Tests A–G against a
dedicated synthetic-data volume).

## Privacy

This repository is public. No real financial data, workbook files, account
numbers or credentials may ever be committed. Development and tests use
synthetic fixtures only — see [SECURITY.md](SECURITY.md) and
[tests/fixtures/README.md](tests/fixtures/README.md).

## License

Personal Use License — see [LICENSE](LICENSE). Free for personal,
non-commercial use and modification. Commercial use, AI/machine-learning
training, and republication as instructional or editorial content are not
permitted.

If this project is useful to you, you can support its development via
[GitHub Sponsors](https://github.com/sponsors/JoshJames04) — thank you.


Budget starts with **All**, combining the visible plans for the selected period.
Choose an owner to edit that owner's budget. Spent so far includes every eligible
outflow through today, even without a budget line or category; refunds reduce it.
Expand **Reconcile spending** for the transaction and split amounts behind the
headline, including budget-covered, unallocated and uncategorised totals. Category
actuals retain the full budget period for historical analysis. Part-paid bills
remain in Bills remaining until their outstanding amount is zero.

Employer payday settings include an optional payroll cutoff day (1–31); leave it
blank to retain calendar-month earning periods. Category pickers sort by name and
native option colours follow the selected light/dark theme and system selection
palette.

Personal monthly budgets now offer a **pay-cycle view**: November's plan uses
the adjusted October payday through the day before November's adjusted payday.
The exact dates are shown below the plan month. This applies to historical plans
without changing transaction dates, approved amounts or revisions. Select the
employment that anchors the cycle when more than one monthly payday is available,
or choose a fixed monthly payday before employment/salary setup is complete.
Missing or ambiguous configuration shows a calendar fallback warning. Payroll
overtime cutoffs remain separate from the spending cycle.

**Shared** and **All** stay on calendar months. Shared shows verified net
contributions into joint accounts, with other external income separate. All
counts household external income once, excluding internal contributions.
Expand received-income/contribution details to see actual money outside the
manual **Income plan**, open its transaction and reconcile category totals to
the in-plan and outside-plan income subtotals. All also shows read-only member
and Shared sections for the same calendar dates. Missing plans are labelled;
their authorized account actuals still contribute to All. Section category tables
include receipts and spending outside the plan, with separate actual totals.
**Opening funds** are separate ledger balances through
the stated date; future periods show currently known funds, not future receipts.
