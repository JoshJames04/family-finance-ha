# Family Finance — Home Assistant add-on

Self-hosted household finance for Home Assistant — budgets, forecasts,
payroll, subscriptions, bank sync and migration from the legacy
spreadsheet.

## What it does

- Budgets and a forecast engine with expected-movement matching
- Bills and commitments alongside subscriptions, with price-change
  detection
- Bank sync (Starling) with account discovery, plus CSV/OFX/QIF
  statement imports for anything else
- Household sharing — per-member accounts, loans and the shared pot
- Savings goals and seasonal events (Christmas, birthdays)
- Payroll, overtime and piecework tracking
- Notifications, plus sensors and automation triggers through the
  `family_finance` custom integration

## Access

Runs entirely through ingress — open **Finance** in the Home Assistant
sidebar. No ports to expose.

## Options

- `admin_token` — required for the admin backup/restore API
  (`POST /api/v1/admin/backups`, `POST /api/v1/admin/restore`).
  Leave unset to keep those endpoints closed.
- `backup_dir` — where database snapshots are written. Point it at
  `/share/...` (e.g. `/share/family-finance`) to make snapshots
  reachable through the Samba or File editor add-ons — the easiest way
  to seed data onto a fresh install. Defaults to the add-on's own
  storage.

## Data

Data lives in the add-on's own storage and is covered by Home Assistant
backups. The app is the system of record: the legacy spreadsheet is
used once, as a migration source, never at runtime.

## Updating

The Supervisor offers a new version when the repository's
`addon/config.yaml` version increases; the image is pulled from
`ghcr.io/joshjames04/family-finance-ha` for both amd64 and aarch64.

## Links

- Repository: https://github.com/JoshJames04/family-finance-ha
- Issues: https://github.com/JoshJames04/family-finance-ha/issues
