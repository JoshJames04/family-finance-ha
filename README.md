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

Employer-specific [bonus planning](docs/BONUSES.md) supports salary percentages, dated shareholdings and manual amounts in one monthly payroll forecast.

## What it does

- **Historical notes** — an administrator can privately view and search retained
  annotations in Settings, preserving original year, sheet and cell context.
  Archives remain explicitly unverified until the original source is available;
  no current finance records are linked by guesswork. See
  [the preservation procedure](docs/MIGRATION.md#historical-annotation-preservation).

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
  Safe to spend and Balance forecast share an account selector, initially using
  the signed-in member's current salary payment account. Missing or ambiguous
  salary destinations prompt for an account instead of guessing.

Manual energy entries represent the kWh consumed on the selected day. Goal progress and energy panels show a retry when a read fails. Subscription summary figures cover active services across both register tabs.

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

Frontend CI and production asset builds use Node.js 22.22.2 or later in the 22.x line. Docker Compose frontend development uses Node.js 24.15.0 or later in the 24.x line. These minimums meet jsdom 31 requirements. Keep `@types/node` on the 22.x API line so shared tooling stays within the production build runtime API; update older local Node patches.

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

Home Assistant private payroll reads are off by default. A signed-in administrator
can select named payroll profiles and private pay destinations in Settings, then
revoke access at any time. Grants cover payroll events and payday discovery and never permit
private banking reads or payroll edits. Human payroll changes require their owner,
including for administrators. See [HA payroll privacy](docs/NOTIFICATIONS-HA.md#private-payroll-reads)
for Safe/Detailed behavior, retained HA history and existing service actions.

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


Dashboard and Forecast default to the signed-in member's salary payment account
when their active employments point to one available account. Otherwise, the
account selector lets them choose which account to forecast. The balance chart
uses that account's scheduled movements. The calendar defaults to the same
account and can also show all visible accounts. Before an account is selected,
the calendar shows all visible accounts and labels that scope.

Budget starts with **All**, combining the visible plans for the selected period.
The section tables show planned spending and income alongside their actual totals.
Choose a budget scope to edit its plan. A line's adjustment dialog can remove
the same line from future editable plans in that scope while keeping current,
historical and locked plans. Archived categories keep their names in saved plans
and are skipped when copying a plan forward. Planned categories are maintained
separately from bills, whose payment account determines their scope. Spent so far
includes all eligible outflow through today. It includes outflow without a budget
line or category and is reduced by refunds.
Expand **Reconcile spending** for the transaction and split amounts behind the
headline, including budget-covered, unallocated and uncategorised totals. Category
actuals retain the full budget period for historical analysis. Part-paid bills
remain in Bills remaining until their outstanding amount is zero. On **Bills**,
open **Overdue** to see past occurrences on non-archived accounts still awaiting
recorded settlement. The list shows any residual amount after linked payments.
Occurrences on archived accounts stay in history. Bank reconciliation checks that both
transfer accounts are open when proposing and confirming a match. Current scheduled cash flow, unpaid bill
totals, funding requirements and reminders exclude those accounts. Accountless household
plans remain visible. A bill keeps its recorded settlements and write-offs when its
account is merged. Overlapping occurrences of the same bill must be resolved before merging. A queued transfer match expires if its expected movement is removed.
Before merging accounts, move active non-bill schedules and resolve outstanding non-bill
movements on the source account.
Bills and their occurrences move with the merge.

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
Shared can save an equal split, custom percentages, or fixed contributions
with one member covering the remainder. New shares can follow the member's
configured monthly payday or a selected day of the month. The setup previews
the amount and first payment date and creates a forecast schedule in Family
Finance. The account holder still makes the transfer at their bank.

When the Shared target changes, Family Finance proposes a new amount to each
affected contributor by default. Open the notification or go to Shared to
accept the next amount or keep the agreed amount. Household admins can
designate an approver or allow automatic changes with a notification.

Bills and Subscriptions offer a payment-account filter below All, Mine and
Shared. Each list offers accounts used on that page. Open Mine for personal
accounts or Shared for joint accounts. The figures follow the account filter.

On Shared, choose the account you and other members pay into. Adjust split uses
the Shared expense total as a suggested funding target. For two members, editing
one amount allocates the remainder to the other. The transfers change only when
you save. Each contribution card shows the member's Budget pay cycle and the
next payment separately. Members without a monthly payday use the calendar
period. The shared budget and transfers between shared accounts keep their
existing calendar and accounting treatment.

Automatic payroll forecasts, optional payslip confirmation and the five local acceptance
checks are documented in [Payroll reliability](docs/PAYROLL-RELIABILITY.md).

Reports display net savings in pounds, the savings rate as a percentage, and changes in that rate in percentage points.
