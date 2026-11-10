# Changelog

All notable changes to Family Finance are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versioning follows [Semantic Versioning](https://semver.org/).

`backend/app/__init__.py` is the single version source of truth;
`scripts/version.py check` enforces consistency across pyproject,
frontend, HA manifest and the add-on config. Tags `v*` trigger the
release pipeline (multi-arch GHCR image + GitHub release).

## [Unreleased]

## [1.3.0] — 2026-11-10

### Added

- **In-app Starling sync interval** (#431): Settings now exposes a
  persistent, admin-gated sync interval (15–1440 minutes, default
  240) applied to the live scheduler without a restart. An env or
  add-on options override still wins and renders the control
  read-only; the effective source is surfaced in the response.
- **Automatic reconciliation of bank payments** (#429, #412): after
  each Starling sync, identifiable incoming payments auto-settle the
  matching expected occurrences — bills mark paid, budgets and
  expected movements reconcile without manual ticking, and already
  paid bills no longer display as overdue.
- **Durable integration event outbox** (#437): domain events now
  persist to an outbox and are exposed through a cursor feed API,
  so the Home Assistant integration can replay events it missed
  across restarts instead of relying on live delivery —
  foundation work for the automation v2 rollout.

### Fixed

- **Dashboard widgets stuck empty after a transient failure**
  (#430, #414): a one-shot `useHouseholds` failure no longer blocks
  widget readiness forever — widgets retry, recover and populate
  once data loads.
- **Horizontal overflow on narrow screens** (#413): unbounded card
  grids no longer push the whole page sideways; a system-wide audit
  added `min-w-0` constraints, horizontal scroll wrappers on exposed
  tables and a wrapping Settings theme row, covered by a new e2e
  sweep across 24 routes at 320px.
- **CI scheduler-tick race** (#444, #447): the test-mode scheduler
  no longer ticks inside tests (it was delivering notifications via
  the real notifier, failing on DNS in CI). Dict-mode
  `Settings.load` defaults the tick to 0; tests opt back in with
  `FF_SCHEDULER_TICK_SECONDS`.

### Changed

- **License** — replaced MIT with the Family Finance Personal Use
  License: free for personal, non-commercial use and modification;
  commercial use, AI/ML training and republication as instructional
  content are not permitted. Container image license label updated
  to `LicenseRef-FamilyFinance-PersonalUse`, and GitHub Sponsors
  funding is enabled for the repository.
- **Generic naming** — internal code, fixtures and docs were
  generalised (#443): no household-specific identifiers remain in
  the codebase or tooling.

### Removed

- **Retired workbook migration machinery** (#445): the one-shot
  Excel workbook import pipeline — API endpoints, `ImportBatch`/
  `ImportRecord` models and tables, migration flag UI, piecework
  import flow and synthetic fixtures — is deleted. Alembic
  migration `0065_drop_workbook_import` drops the retired tables.
  Live statement import (CSV/OFX/QIF) is unaffected.

## [1.2.0] — 2026-11-10

### Added

- **Native Home Assistant automation triggers** (#424): the
  integration device now offers all eight `family_finance_*` events
  in the automation editor's device trigger list via a
  `device_trigger` platform — no hand-written event YAML needed.
  Two new transitions: `family_finance_bill_overdue` (past-due
  unmatched-commitment count rising) and `family_finance_payday`
  (`next_payday` rolling forward). Full trigger table, payload
  schemas and UI/YAML examples documented in `docs/HA-DEPLOYMENT.md`.
- **Opt-in Starling V2 webhook receiver** (#422):
  `POST /api/v1/integrations/starling/webhook` — registered only
  when `FF_STARLING_WEBHOOK_PUBLIC_KEY` is configured. Verifies
  `X-Hook-Signature` (SHA512withRSA over the raw body) against the
  configured public key, then flags `refresh_requested` on the
  affected account's cursor; the scheduler drains it through the
  normal pipeline within one tick. Polling stays the default and
  remains the fallback; new `starling_webhook_public_key` add-on
  option + setup guide in `docs/OPERATIONS.md` §10.
- **Scheduled balance and discovery sync** (#416): `sync_tick`
  previously only pulled transaction feeds, so balances, account
  renames and closures never updated automatically. Due connections
  now run the same pipeline as manual sync (discovery → balances →
  feed); diagnostics expose the configured cadence.
- **"Bills remaining" budget KPI** (#415): new `bills_remaining`
  field on `/budgets/{id}/actuals` — unpaid commitment/subscription
  expected movements in the window (overdue included, owner-scoped).
  Fifth KPI tile distinguishing planned / spent / budget remaining /
  bills remaining, with explicit overspend rendering; semantics
  documented in `docs/DATA-MODEL.md`.
- **Starling webhook feasibility spike** (#417):
  `docs/spikes/0417-starling-webhooks.md` — PAT V2 webhooks exist
  (feed-item + standing-order events, public-key signatures) but
  require a public HTTPS callback; polling remains the default.

### Fixed

- **Bills "Next due" showing a past date** (#412): past `next_date`
  values now count on a separate Overdue tile (count + oldest);
  Next due shows the earliest date ≥ today or "—".
- **Bills summary tiles on mobile** (#413): summary grid reflows
  1→2→3→5 columns with safe minimum widths — no horizontal scroll
  down to 320px.
- **Dashboard widgets disappearing on return** (#414): a failed or
  hung households probe left the layout permanently unhydrated —
  blank grid until Reset. Hydration now falls back to the local
  layout on probe failure and renders skeletons while pending.

## [1.1.2] — 2026-11-09

### Added

- **2026-27 UK payroll rule sets** (#399): published statutory
  parameters for `england_wales` (frozen PA/bands, NI cat A 8%/2%,
  student-loan Plan 2 £29,385) and `scotland` (raised 19/20/21% band
  limits) — 2026-27 payroll now computes with exact rules instead of
  the 2025-26 fallback. Golden-suite coverage including a
  duty-allowance allowance-line case.
- **`tools/release_gate.py` + `tools/perf_check.py`** (#372): ported
  from the closed PR #261 — 7 machine-checkable release gates and a
  standalone ~50k-row hot-path budget fixture.
- **`docs/OPERATIONS.md`** (#368): clean-operator runbook — install,
  probes, backup/restore, Starling re-auth, upgrade rollback,
  diagnostics, logs and scheduler ticks.
- **Ingress trust audit matrix** (#364): `TestIngressTrustMatrix`
  pins the supervisor boundary — XFF cannot launder the peer IP,
  partial header sets denied, remote headers rejected in standalone
  mode, spoof beats a valid session, headerless requests fail closed.
  Findings documented in `docs/SECURITY.md` §2.1.

### Fixed

- **Member-switcher identity spoof residual** (#341): under ingress,
  `X-Profile-Id` is no longer sent on any request, `useIdentity` no
  longer writes a fallback to localStorage, and "Mine" scoping
  resolves from `/api/v1/auth/me` via a new `useMeProfileId` hook.

### Changed

- **README status** now reflects shipped reality — released to GHCR,
  workbook retired 2026-11-09.
- **Changelog reconciled** — 1.1.0/1.1.1 entries and
  `addon/CHANGELOG.md` back-merged from `main` (they had only existed
  on release branches).

## [1.1.1] — 2026-11-09

### Changed

- **Integration renamed to "Family Finance (Entities)"** — the add-on
  and the HACS integration showed identical update cards; they now
  read as two distinct components.

### Added

- **`addon/CHANGELOG.md`** — the Supervisor update card shows release
  notes instead of "No changelog found".

## [1.1.0] — 2026-11-09

### Added

- **Link HA identity to an existing profile** (#340): admin action
  that attaches a Home Assistant user to a pre-existing household
  member instead of always creating a new profile.
- **Backup list UX** (#371): newest-first ordering by parsed
  timestamp, a `Latest` badge, and human-readable labels via the
  shared formatter.
- **Add-on icon + logo** (#401): `icon.png`/`logo.png` rendered from
  the brand SVG — the add-on no longer shows "No app icon".
- **Migration parity tooling** (#387, #389): `tools/reconcile_parity.py`
  recomputes workbook totals against the imported database at
  row/month level; `docs/PARITY-MATRIX.md` maps every worksheet to
  its domain, UI surface and tests.
- **Cross-install restore e2e** (#375): snapshot → new install →
  token re-rotation covered end-to-end.
- **Performance budget guardrails** (#132): measured budgets with
  CI enforcement.
- **Bank-ledger integrity fixes** (#350): duplicate bills and
  subscriptions, Starling account discovery/ownership, member-scoped
  loans, permanent deletion/archive behaviour, Christmas gift
  reconciliation, account editing, and migration export/import.

### Fixed

- **Budget parity drift** (#388): `BudgetYearExtractor` only
  imported literal month cells — mid-year edits stored as formulas
  were silently dropped. Formula cells now resolve through the
  workbook cached-values map; all 169 imported budget rows
  reconcile at month-cell level.
- **Backup button double-fire** (#371): Back up now disables for
  the request plus a 1.5 s cooldown, so same-second snapshot
  collisions can't be created from the UI.

### Changed

- **Lint gates are blocking** (#373): ruff and mypy failures now
  fail CI instead of reporting.
- **Same-PR doc updates required** (#376, #385): behaviour,
  security and ops changes must update their docs in the same PR.
- **Workbook retirement sign-off recorded** (#391, #56): owner
  approval + 2026-11-09 retirement date in `docs/MIGRATION.md`
  §7.1; defect-B flag disposition documented.

## [1.0.5] — 2026-11-09

### Fixed

- **Blank white panel under ingress**: `BrowserRouter` used
  `basename="/"` but HA mounts the app at
  `/api/hassio_ingress/<token>/`, so the router refused to render.
  The basename is now derived from `window.location` at startup —
  the ingress prefix when present, `BASE_URL` standalone.
- **Add-on restart loop**: the Docker `HEALTHCHECK` had no
  `start-period`, so health probes failed during the long
  first-boot migration, marked the container unhealthy and the
  Supervisor restarted it — killing it mid-migration in a loop.
  `start-period=120s` covers the boot window.
- (v1.0.4 was tagged but its release build failed on a strict-TS
  error in `ingress.ts` — skipped; 1.0.5 contains it and the fix.)

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
