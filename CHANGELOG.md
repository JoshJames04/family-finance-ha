# Changelog

All notable changes to Family Finance are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versioning follows [Semantic Versioning](https://semver.org/).

`backend/app/__init__.py` is the single version source of truth;
`scripts/version.py check` enforces consistency across pyproject,
frontend, HA manifest and the add-on config. Tags `v*` trigger the
release pipeline (multi-arch GHCR image + GitHub release).

## [Unreleased]

## [1.0.1] — 2026-11-09

### Fixed

- Add-on options `admin_token` / `backup_dir` defaulted to `null`,
  which the Supervisor rejects on save — they now default to empty
  strings (equivalent to unset).
- `release.yml` accepts `workflow_dispatch` + `tag` input and is
  dispatched directly by `tag-on-main` (GITHUB_TOKEN tag pushes cannot
  cascade); the release step tolerates an already-existing release.

## [1.0.0] — 2026-11-09

First released image (`ghcr.io/joshjames04/family-finance-ha:1.0.0`);
pushes to `main` now auto-tag `v{version}` and fire `release.yml`.

- Transactions: split transactions across categories with
  double-counting guards preserved in all amount aggregates (#289).
- Loans: member-scope filtering + full edit (party, direction,
  principal, expected payment, interest, owner, active) with
  RBAC visibility/write gates matching accounts/budgets (#278).
- Banking: bank-statement file import — CSV/OFX/QIF (#288).
- Shared pot: dedicated Shared page for the household bills pot
  (#277).
- Home Assistant: `family_finance_payslip_review` /
  `family_finance_notification_pending` events plus
  `notifications_pending` / `payslips_pending_review` sensors for
  user automations (#307).
- Release pipeline: `tag-on-main` workflow closes the
  "push to main → HA update offered" path.

## [0.1.0] — development line

- Monorepo platform: FastAPI backend, React/Vite frontend, HA stub,
  docker-compose dev stack.
- SQLite persistence (WAL, FK, busy-timeout) with Alembic migrations,
  upgrade/downgrade harness, pre-migration snapshots and auto-restore.
- Domain core: Money (integer pence), UK dates, transactions,
  transfers, recurring commitments, refunds, reporting-period locks.
- REST `/api/v1` with household/profile scoping, problem-details
  errors, audited mutations and an append-only audit log.
- Migration pipeline: read-only openpyxl parser, staged import with
  provenance, idempotent commit/rollback, defect register A–F.
- Budgets: draft→approved→locked lifecycle, triple-value lines,
  templates/copy-forward, rollover, member scoping, reporting.
