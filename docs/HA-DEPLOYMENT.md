# Home Assistant Deployment Architecture

**Status:** Proposed — validated by Epic 1 spike (add-on vs app packaging).

## 1. Primary model: Home Assistant app (add-on)

Family Finance ships as a containerised Home Assistant app, comparable to the
Grocy HA app.

- **`config.yaml`** — app manifest: name, slug, version (semver), arch list,
  `ingress: true`, `panel_sidebar: true`, `panel_icon`, ports, options schema,
  `homeassistant_api: true` (for integration auth), map/volume for `/data`.
- **Persistent storage** — `/data` volume holds SQLite DB, import staging and
  config; survives app updates; included in HA backups when opted in.
- **Ingress** — UI served through HA ingress (no exposed port needed).
  Sidebar entry via `panel_sidebar`. Session inherited from HA; app still
  enforces its own authz (ADR-0005).
- **Options/secrets** — non-secret config via app options; secrets (Starling
  tokens) in the app's encrypted vault, never in `options.json` plaintext.
- **Health** — watchdog healthcheck on `/healthz`; Supervisor restart policy.

## 2. Repository/distribution

- App image built per-arch (amd64/aarch64) via GitHub Actions; tagged semver.
- Distributed via a HA add-on repository descriptor (`repository.yaml`) —
  either this repo or a dedicated `family-finance-ha-apps` repo (decision in
  Epic 1 spike).

## 3. Secondary model: HA Container / standalone Docker

Same image runs standalone for HA Container users:

- No ingress → UI on a mapped port behind the user's own reverse proxy/auth.
- Env-file configuration instead of app options.
- HA API access via long-lived token (documented least-privilege setup).
- Feature parity goal; ingress-only conveniences degrade gracefully
  (documented differences, never silently absent).

## 4. Custom integration

`custom_components/family_finance` (installed via HACS or manually):

- Connects to the app API (local HTTP) — read model only.
- **Sensors:** month budget spend/remaining, month-end surplus forecast,
  next payday, safe-to-spend, upcoming bills, budget variance, savings goal
  progress, sync status, estimated electricity cost.
- **Services:** e.g. `record_overtime`, `refresh_bank_sync`,
  `add_transaction` — validated, authenticated, audited.
- **Events/triggers** for automations (bill due, sync failure, milestone).
- **Privacy:** sensors opt-in; sensitive values (balances) can be configured
  off; nothing personal in entity names.
- Compatibility pinned to a supported HA version range, tested in CI matrix.

## 5. Backup, upgrade, rollback

- App data participates in HA backup/restore; plus app-level scheduled
  `VACUUM INTO` snapshots with rotation (see Epic 17).
- Upgrades: DB migrations run at app start, pre-migration snapshot
  automatically created; failed migration → automatic restore of snapshot and
  refuse to start (logged, health endpoint reports failure).
- Rollback: install previous image; downgrade migrations tested in CI.
- Data corruption guard: integrity check (`PRAGMA integrity_check`) on boot
  and scheduled.

## 6. Environments

- **dev** — local docker-compose (app + stub HA API) or devcontainer; seed
  synthetic fixtures.
- **ci** — GitHub Actions service containers + synthetic data.
- **prod** — the user's HAOS instance. No prod financial data in dev/CI.
