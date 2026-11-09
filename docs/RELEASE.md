# Release process

Disciplined releases for Family Finance: semantic versioning, a
kept changelog, a pre-release gate, and a rehearsed rollback path
(issue #133).

`main` is production/live only; `develop` is the integration line.
A release is the act of promoting `develop` to `main` and tagging.

## Versioning rules

- [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.
  - `MAJOR` — breaking API or migration that cannot auto-upgrade.
  - `MINOR` — backwards-compatible features.
  - `PATCH` — backwards-compatible fixes.
- `backend/app/__init__.py::__version__` is the **single source of
  truth**. `scripts/version.py` keeps it in lockstep with
  `backend/pyproject.toml`, `frontend/package.json`,
  `custom_components/family_finance/manifest.json` and
  `addon/config.yaml`:

  ```bash
  python scripts/version.py set 1.2.0   # bump everywhere
  python scripts/version.py check       # CI gate — drift fails
  ```

- The HA manifest, add-on config and container image
  `org.opencontainers.image.version` label all derive from this
  value — never edit them by hand.

## Changelog rules

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/):

- Accumulate notable changes under `## [Unreleased]` during the cycle
  (`Added` / `Changed` / `Fixed` / `Removed` / `Security`).
- At release, rename `[Unreleased]` to `[X.Y.Z] — YYYY-MM-DD` and add
  a fresh empty `[Unreleased]` section.
- Entries are user-facing: an issue number and what changed, not a
  commit list (the release notes generator already produces that).

## Pre-release gate

All of these MUST pass before tagging. The first four are already CI
jobs; the last three are manual evidence.

| Gate | How to run | Pass condition |
|---|---|---|
| Full CI on `develop` | `gh run list -b develop` | every job green |
| Version consistency | `python scripts/version.py check` | no drift |
| Migration dry-run | `cd backend && alembic upgrade head && alembic downgrade -1 && alembic upgrade head` on a **copy** of the production DB | clean both directions |
| Backup verified | `POST /api/v1/admin/backups`, then confirm the snapshot appears in `GET /api/v1/admin/backups` and `verify_snapshot` passes | listed + verifiable |
| Forbidden-file / secret scan | CI `forbidden-files`, `secret-scan` jobs | green — no workbook data, no credentials |
| Diff review | `gh pr create -B main -H develop` | aggregate diff reviewed, no surprises |

## Releasing

1. Ensure `develop` is green and the changelog is updated.
2. `python scripts/version.py set X.Y.Z` on a `release/<ver>` or
   `chore/` branch → PR to `develop` (the version bump lands on
   `develop`, then rides to `main`).
3. Open the release PR `develop` → `main`; review the aggregate diff.
4. Merge the release PR to `main`. `.github/workflows/tag-on-main.yml`
   automatically tags `vX.Y.Z` on the merge commit and pushes it —
   no manual tag step (it exits cleanly if the tag already exists).
   Manual fallback if automation fails:

   ```bash
   git checkout main && git pull
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

5. The tag triggers `.github/workflows/release.yml`:
   - **verify** — tag must equal `__version__` and all version
     targets consistent.
   - **smoke** — single-arch image build, boots, `/healthz` OK, API
     version and image label match the tag.
   - **publish** — multi-arch (amd64/arm64) push to GHCR as
     `…/family-finance-ha:X.Y.Z` + `:latest`.
   - **release** — GitHub release with generated notes.
6. Deploy: pull the tagged image (docker-compose) or let the HA
   add-on pick up the new version.

## Post-release verification checklist

Run after every release; record results on the release issue/PR.

- [ ] Release workflow green end-to-end on the tag run.
- [ ] `docker inspect` of the deployed image shows
      `org.opencontainers.image.version` = tag.
- [ ] `GET /healthz` and `GET /readyz` both 200; `/readyz` reports
      `migrations: true`.
- [ ] `GET /api/v1/version` returns the tag version.
- [ ] Fresh pre-migration snapshot exists in the backup dir (taken
      automatically on first boot of the new version).
- [ ] Spot-check: one read endpoint, one audited mutation, one
      report render — all correct on real data.
- [ ] **HA-side smoke**: integration loads, config flow unchanged,
      sensors reporting, `family_finance` services callable, no new
      repair/issue entries.
- [ ] Add-on: update visible in the HA add-on store; watchdog
      (`/healthz`) healthy.

## Rollback plan

Rollback = run the previous image on the previous data. The data
half is already automated:

- Every app boot takes a **pre-migration snapshot** before Alembic
  runs (`app/main.py` lifespan) — a failed migration auto-restores
  and boots degraded rather than corrupting data.
- Snapshots are verified (`verify_snapshot` runs `PRAGMA
  integrity_check`) and rotated (`FF_BACKUP_KEEP`).

Procedure:

1. Identify the pre-upgrade snapshot (`GET /api/v1/admin/backups` —
   timestamped names, latest-before-upgrade is the one).
2. Request restore: `POST /api/v1/admin/restore {"snapshot":
   "<name>"}` — writes the restore marker; on next boot the snapshot
   replaces the live DB **before** the engine opens.
3. Redeploy the previous image tag:

   ```bash
   docker compose pull family-finance:X.Y.Z-1   # pin, not :latest
   docker compose up -d
   # HA add-on: select the previous version in the add-on panel
   ```

4. Verify `/healthz`, `/readyz`, version endpoint, and one real-data
   read.
5. If the release PR merge is the problem, revert the merge commit on
   `main` and re-tag — never rewrite `main` history.

## Dry-run record

Rollback was exercised in dev (issue #133): a scratch household DB
was snapshotted via `persistence.backup.create_snapshot`, verified
with `verify_snapshot`, then every table was wiped and
`restore_from` brought all 38 tables back — row counts and row
content identical. The marker-based `request_restore` path is
covered end-to-end by `tests/test_backup_restore.py`.
