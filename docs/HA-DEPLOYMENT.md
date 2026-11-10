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
- **Events/triggers** for automations — see the trigger table below.
- **Privacy:** sensors opt-in; sensitive values (balances) can be configured
  off; nothing personal in entity names.
- **Sensor privacy model (implemented):** every monetary-value entity
  (`sensor.family_finance_total_balance`, `..._month_spend`,
  `..._month_remaining`, `..._budget_variance`) is
  `entity_registry_enabled_default=False` — a default install exposes
  counts, dates and statuses only. Each entity must be enabled
  explicitly in *Settings → Devices & Services → Entities*. Entity
  attributes carry only aggregate context (`app_version`, totals) —
  never payees, counterparties, or per-record amounts.
- **Recorder exclusion:** HA's recorder still stores history for any
  enabled entity. Households that enable monetary sensors may exclude
  them from long-term history via `configuration.yaml`:

  ```yaml
  recorder:
    exclude:
      entity_globs:
        - sensor.family_finance_total_balance
        - sensor.family_finance_month_spend
        - sensor.family_finance_month_remaining
        - sensor.family_finance_budget_variance
  ```

  Exclusion is a hardening choice, not a requirement — the entities
  exist only because the household enabled them.
- Compatibility pinned to a supported HA version range, tested in CI matrix.

### Automation triggers (issue #424)

Family Finance appears in the automation editor under its device —
*Settings → Devices & Services → Family Finance → ⋯ → Add automation*
(or *Automations → New → Device → Family Finance*). Each trigger maps
1:1 to a `family_finance_*` bus event; events fire only on the
documented transition — coordinator restarts and unchanged polls
never re-fire.

| Automation-editor trigger | Bus event | Fires when | Payload |
|---|---|---|---|
| A bill is coming due | `family_finance_bill_due` | upcoming-bill count **rose** | `bills_due`, `window_days` |
| A bill became overdue | `family_finance_bill_overdue` | overdue-commitment count **rose** | `overdue_count` |
| Bank sync failed | `family_finance_sync_failed` | a poll failed — **once per failure streak** | `sync_status` |
| A savings goal milestone was reached | `family_finance_goal_milestone` | goals-on-track count **rose** | `goals_on_track`, `goals_total` |
| Total balance dropped below zero | `family_finance_low_balance` | total balance **crossed below** zero | `account_count`, `threshold_pence` |
| A notification is pending | `family_finance_notification_pending` | unacknowledged-event count **rose** | `pending_count` |
| Payday was reached | `family_finance_payday` | `next_payday` **rolled forward** | `reached`, `next_payday` |
| A payslip needs review | `family_finance_payslip_review` | pending-review count **rose** | `pending_count` |

Payloads are counts, dates and enum strings only — never payees,
notes, account identifiers or per-transaction amounts.

**UI-built automation** (what the editor produces for "A bill became
overdue" on the Family Finance device):

```yaml
trigger:
  - platform: device
    device_id: <family-finance-device-id>
    domain: family_finance
    type: bill_overdue
```

**YAML alternative** — raw event triggers accept the same payloads:

```yaml
trigger:
  - platform: event
    event_type: family_finance_bill_overdue
action:
  - service: notify.notify
    data:
      title: "Bills overdue"
      message: "{{ trigger.event.data.overdue_count }} bill(s) overdue"
```

**Not exposed as triggers** (assessed and rejected): raw
"transaction imported" events (a count would spam and payloads would
risk identifiers — use `notification_pending`, which already covers
review work), "bill paid" (a count *drop* also fires on dismissal —
ambiguous), "goal achieved" (the summary exposes on-track only —
`goal_milestone` is the reliable signal).

## 5. Backup, upgrade, rollback

- App data participates in HA backup/restore; plus app-level scheduled
  `VACUUM INTO` snapshots (`FF_BACKUP_INTERVAL_HOURS`, default 24h) with
  keep-N rotation (`FF_BACKUP_KEEP`, default 7) under
  `<data_dir>/backups` (see Epic 17).
- Upgrades: DB migrations run at app start; a pre-migration snapshot is
  taken automatically. A failed migration restores the snapshot, boots
  the app degraded, and `GET /readyz` reports `503` via the `boot`
  component — data is never left half-migrated. An unreachable DB file
  still fails fast at boot.
- Manual backup/restore: `POST /api/v1/admin/backups` and
  `POST /api/v1/admin/restore`, gated by `X-Admin-Token`
  (`FF_ADMIN_TOKEN`; fail-closed when unset) and audited. Restore never
  replaces the live file — it verifies the snapshot's integrity and
  writes `<data_dir>/restore.pending`, applied at the next boot before
  the engine opens.
- Rollback: install previous image; downgrade migrations tested in CI.
- Data corruption guard: integrity check (`PRAGMA integrity_check`) on
  boot and on every snapshot.

## 6. Environments

- **dev** — local docker-compose (app + stub HA API) or devcontainer; seed
  synthetic fixtures.
- **ci** — GitHub Actions service containers + synthetic data.
- **prod** — the user's HAOS instance. No prod financial data in dev/CI.
