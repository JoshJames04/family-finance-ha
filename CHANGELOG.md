# Changelog

All notable changes to Family Finance are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versioning follows [Semantic Versioning](https://semver.org/).

`backend/app/__init__.py` is the single version source of truth;
`scripts/version.py check` enforces consistency across pyproject,
frontend, HA manifest and the add-on config. Tags `v*` trigger the
release pipeline (multi-arch GHCR image + GitHub release).

## [Unreleased]

## [1.0.3] — 2026-11-09

### Fixed

- **Startup crash on the second and every later add-on boot**: the
  Supervisor serialises unset options as `""`, and `backup_dir: ""`
  parsed as `Path("")` — truthy — so `resolved_backup_dir` became a
  bare relative name and the pre-migration `VACUUM INTO` snapshot
  failed trying to write under `/app`. `""` option values now
  normalise to unset, and `resolved_backup_dir` falls back on
  blank paths.
- `require_admin` treated `admin_token: ""` as configured, so an
  explicit empty `X-Admin-Token:` header could satisfy the
  comparison — empty tokens now count as unset (fail-closed).
- `docker-entrypoint.sh` CRLF hardening: the Dockerfile strips
  carriage returns at build time and `.gitattributes` pins
  `*.sh eol=lf`, so Windows checkouts can't break the shebang.

### Added

- `release.yml` smoke now runs a Supervisor-mode pass: root-owned
  `options.json` with `""` values, `SUPERVISOR_TOKEN`, a named
  `/data` volume, a supervisor-IP client, and a restart to cover
  the existing-DB snapshot path — ingress identity bootstrap,
  SPA serving and `/healthz` all asserted.

## [1.0.2] — 2026-11-09

### Fixed

- Add-on served a blank ingress panel: nothing enabled
  `FF_INGRESS`, so supervisor `x-remote-user-*` headers were
  ignored and every request resolved anonymous. The entrypoint
  now maps the Supervisor-injected `SUPERVISOR_TOKEN` to
  `FF_SUPERVISOR_TOKEN` and enables `FF_INGRESS` whenever it is
  present (overridable; standalone runs unaffected).
- Fresh installs had no `Household` row for ingress identity to
  provision into — `resolve_identity` now materialises the
  household on the first supervisor request so the first HA user
  bootstraps as admin.
- Removed the `watchdog` line — a port-mapped watchdog could
  restart-loop the add-on during the long first-boot migration.

## [1.0.1] — 2026-11-09

### Fixed

- Add-on options `admin_token` / `backup_dir` defaulted to `null`,
  which the Supervisor rejects on save — they now default to empty
  strings (equivalent to unset).
- Container start crashed with `PermissionError` reading
  `/data/options.json` (the Supervisor writes it root-owned) —
  a `docker-entrypoint.sh` now chowns `/data` before dropping to
  the unprivileged `ff` user.
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
