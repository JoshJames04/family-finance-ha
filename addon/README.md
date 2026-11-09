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
sidebar. Publishing port `8099` under the add-on's *Network* section is
optional and only needed if the `family_finance` custom integration or
the admin API should reach the app at `http://<ha-host>:8099` directly.

## Options

- `admin_token` — unlocks the admin backup/restore API for
  out-of-band callers. Leave unset to keep those endpoints closed to
  anything but an admin member signed in through ingress.
- `backup_dir` — where snapshots are written. Point it at
  `/share/...` to reach them through the Samba or File editor add-ons;
  not needed for the download/upload flow.

## Data

Data lives in the add-on's own storage and is covered by Home Assistant
backups. The app is the system of record: the legacy spreadsheet is
used once, as a migration source, never at runtime.

Moving an existing install's data here is two clicks: **Settings →
Data & backups → download icon** on a snapshot on the old install,
then **Restore from file…** on this one. The restore applies on the
next add-on start and replaces everything currently stored.

## Updating

The Supervisor offers a new version when the repository's
`addon/config.yaml` version increases; the image is pulled from
`ghcr.io/joshjames04/family-finance-ha` for both amd64 and aarch64.

## Links

- Repository: https://github.com/JoshJames04/family-finance-ha
- Issues: https://github.com/JoshJames04/family-finance-ha/issues
