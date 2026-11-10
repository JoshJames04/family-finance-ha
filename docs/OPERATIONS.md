# Operations Runbook — Family Finance

Day-2 operations for the add-on: install, health, backup/restore,
bank re-auth, upgrade rollback and diagnostics. Written for a clean
operator — every step uses the UI or a documented endpoint.

## 1. Install & setup

| Component | Where | Notes |
|---|---|---|
| App (add-on) | Settings → Add-ons → Family Finance | Updates ship via the add-on store; update the add-on **first**, then the integration |
| Entities (integration) | HACS → Family Finance (Entities) | Restart HA after updating |
| Shared data | `/share` mapped volume | The DB lives under the add-on's `/data`; snapshots under the configured `backup_dir` |

Post-install: open the sidebar panel — the first HA user becomes the
admin profile automatically (no setup wizard needed).

## 2. Health & readiness

| Probe | Endpoint | Meaning |
|---|---|---|
| Liveness | `GET /healthz` | Process is up — always 200 |
| Readiness | `GET /readyz` | Component checks (db, migrations, boot); `degraded` → 503 with per-component detail |

When the add-on shows "unhealthy": read the add-on Log first — the
120 s healthcheck grace covers alembic migrations on big databases.

## 3. Backups

- **Take a snapshot:** Settings → Backup → *Take snapshot*
  (or `POST /api/v1/admin/backups`). The button locks while a
  snapshot runs and the list shows newest-first with a `Latest`
  badge.
- **Every app boot** also writes a **pre-migration snapshot** before
  Alembic runs — the latest-before-upgrade snapshot is the rollback
  anchor.
- **Download:** `GET /api/v1/admin/backups/{name}/download` — keep an
  off-box copy before any risky operation.

## 4. Restore

- **Same install:** `POST /api/v1/admin/restore` replaces the live
  DB — it closes all sessions, swaps the file and re-checks
  integrity on next boot.
- **Move installs (upload):** `POST /api/v1/admin/restore/upload`
  accepts a downloaded snapshot file. Restored uploads appear in the
  backup list under their uploaded name — verify the row you
  intended before restoring.
- **After a restore:** bank connections need re-authentication
  (§5) — the vault key does not travel with the backup by design.

## 5. Bank connection re-auth (Starling)

Symptoms: sync runs failing with `401`/`422 re-authenticate` in the
add-on log or Settings → Banking showing a degraded connection.

1. Generate a new personal-access token in Starling (same scopes:
   accounts, balances, transactions read).
2. Settings → Banking → connection → **Rotate token**
   (`POST /api/v1/banks/connections/{id}/rotate`).
3. Trigger a manual sync — the cursor clears once a run succeeds.

Rate limiting is handled by `retry_at` backoff — a `429` shows as a
deferred `retry_at` on the sync cursor in `/diagnostics`; the
scheduler never hammers the provider.

## 6. Upgrade rollback

If an upgrade boots badly:

1. Stop the add-on; restore the **pre-migration snapshot** via
   `POST /api/v1/admin/restore` (or copy it back over `/data` if the
   app won't boot — the snapshot is a plain SQLite file).
2. Downgrade the add-on to the previous store version.
3. If the DB already migrated forward, alembic will refuse to run
   against the older app — restore first, then downgrade.

## 7. Diagnostics

`GET /api/v1/diagnostics` returns a sanitised JSON bundle:

- `app.version`, `database.alembic_revision`, per-table row counts
- `scheduler` — configured `tick_seconds` + `sync_interval_minutes`
- `sync[]` — per-account cursor status, `retry_at`, last success
- `bank_connections[]` — provider status + sanitised last error
- `recent_failures` — last 10 failed sync runs + notification
  deliveries + pending merge-candidate count

**Privacy boundary:** amounts, payees, refs and any digit runs in
free-text fields are redacted before the response leaves the process
(`test_no_financial_values_or_identifiers`). It is safe to attach to
a support issue as-is.

## 8. Logs

All logs are structured JSON (`ts`, `level`, `logger`, `msg`,
`request_id`). Secret-shaped values (Bearer tokens, `token=`/
`password=`/`secret=`/`api_key=` params and keys) are redacted by
`SecretRedactionFilter` — verified by `test_logging_and_errors`.

- Add-on log: Settings → Add-ons → Family Finance → Log.
- Notification-rule failures log `notification rule <id> evaluation
  failed` — the rule id, never the rule content.

## 9. Scheduler

Two ticks run from the app lifespan (`sync_tick`, `notify_tick`):

- `sync_tick` drains queued manual refreshes past their backoff
  window and runs due scheduled syncs (default cadence 4 h). For each
  connection with due work it first refreshes provider state —
  account discovery (renames/closures) and live balances — then pulls
  the transaction feeds, matching what the manual Sync button does.
  A provider error on the refresh marks `conn.last_error` but never
  starves the feed pull; one mapping's failure marks its own
  run/cursor — never aborts the tick.
- `notify_tick` evaluates enabled rules and delivers pending
  notification deliveries. A rule's exception is logged and the tick
  continues.

## 10. Data safety reminders

- `Family Budget.xlsx` and real financial data must never be
  committed or posted — the repo is public.
- The workbook is a one-time migration source; the app is the system
  of record since the 2026-11-09 retirement sign-off.
