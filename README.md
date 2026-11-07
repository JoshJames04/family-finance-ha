# Family Finance

A self-hosted household finance application for Home Assistant. Family Finance is
designed to become the single source of truth for a household's financial life:
budgeting, forecasting, payroll, savings, debts, subscriptions and day-to-day
spending — all in one place, under your control.

> **Status:** Pre-implementation planning. The development backlog lives in
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

- **Budgeting** — monthly, annual and custom-period budgets with original
  budget, revised forecast, actuals, variance and forecast-accuracy tracking.
- **Cash-flow forecasting** — date-aware projected balances, safe-to-spend,
  payday intelligence and overdraft risk warnings.
- **UK payroll** — salary history, overtime (configurable multipliers), PAYE,
  National Insurance, student loans, pension and payslip reconciliation.
- **Banking** — secure, read-only Starling Bank synchronisation with duplicate
  detection, automatic categorisation and reconciliation. The app is fully
  functional without it.
- **Household contributions** — configurable transfer rules between personal,
  joint and bills accounts, with outstanding-transfer detection.
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
- [Roadmap](docs/ROADMAP.md)
- [Contributing & Standards](CONTRIBUTING.md)
- [Architecture Decision Records](docs/adr/)

## Development

All work flows through GitHub Issues: epics → user stories/tasks → pull
requests against `main`. See [CONTRIBUTING](CONTRIBUTING.md) for branching,
Definition of Ready/Done, labels and review standards.

## Privacy

This repository is private. No real financial data, workbook files, account
numbers or credentials may ever be committed. Development and tests use
synthetic fixtures only — see [SECURITY.md](SECURITY.md) and
[tests/fixtures/README.md](tests/fixtures/README.md).

## License

MIT — see [LICENSE](LICENSE).
