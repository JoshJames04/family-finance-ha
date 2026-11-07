# Spike #18: Home Assistant app packaging and ingress approach

Status: complete (decision implemented in #23; this doc records the
evaluated options and rationale)
Date: 2026-11-08
Sources: developers.home-assistant.io add-on tutorial + add-on
configuration reference, Supervisor ingress docs, add-on security
guide, repository/community add-on conventions.

## Options evaluated

### 1. HA Community Add-on style local build (Dockerfile in repo)

The Supervisor builds the image on-device from `Dockerfile` +
`build.yaml`. Works without any registry, but builds run on the HA
host (a Pi rebuilds Node + pip every upgrade) and `build.yaml` pins
base images per-arch.

### 2. Pre-built image from GHCR (chosen)

`addon/config.yaml` carries an `image:` field pointing at a
multi-arch manifest (`ghcr.io/joshjames04/family-finance-ha`).
Release CI builds `amd64` + `aarch64` once via
`docker/build-push-action`; the Supervisor pulls instead of
compiling. Faster installs, identical bits on every device, and the
image doubles as the standalone-container artefact (ADR-0002).

### 3. HA App / custom integration (rejected)

HACS integrations run inside the HA Python env — no FastAPI server,
no separate SQLite, no isolated upgrade path. Wrong abstraction for
an app that owns its own persistence and release cycle.

## Findings

### Ingress is the only sensible UI path

- `ingress: true` + `panel_sidebar: true` in `config.yaml` mounts
  the app under a per-install `/api/hassio_ingress/<token>/` prefix
  with HA auth handled by the Supervisor (see spike #27).
- Consequence for the frontend: **all asset URLs must be
  relative** — `vite.config.ts` sets `base: './'` so the bundle
  works under any ingress prefix. Absolute `/assets/...` paths break
  inside ingress.
- The backend must serve the SPA for arbitrary deep paths (React
  Router navigations arrive as `/api/hassio_ingress/<t>/budgets/…`,
  which ingress strips to `/budgets/…`). A last-registered catch-all
  returns `index.html`; API routes are matched earlier and keep
  their real status codes.

### One image, two run modes

- Add-on mode: `FF_DATA_DIR=/data` (the add-on's persistent mount),
  env from Supervisor.
- Standalone mode: same image, user mounts any dir at `/data` and
  sets `FF_*` vars directly — no add-on-only code paths.

### Watchdog + health

- Supervisor watchdog hits a configurable URL — `/healthz` is the
  app liveness probe and is already the Docker `HEALTHCHECK`.
- The SPA catch-all is registered *after* probes: an early
  `/{path}` route shadows `/healthz`/`/readyz` and returns HTML
  (found in test — ordering matters).

## Decision (implemented by #23)

- Root `Dockerfile`, two stages: `node:22-slim` builds
  `frontend/dist`; `python:3.13-slim` runtime ships `app/`,
  `migrations/`, `alembic.ini`, copies `dist` → `/app/web`.
- Non-root `ff` user (uid 10001), `VOLUME /data`, `EXPOSE 8099`,
  `HEALTHCHECK` on `/healthz`.
- `Settings.resolved_web_dir`: configured `FF_WEB_DIR` → `/app/web`
  (image) → repo `frontend/dist` (dev).
- `addon/config.yaml`: `slug: family_finance`, `image:` GHCR,
  `arch: [amd64, aarch64]`, `ingress`, `panel_sidebar`, `init: false`
  (uvicorn is PID 1 via tini-less exec CMD).
- `release.yml` builds from repo root (the Dockerfile moved up a
  level for access to `frontend/`); `tests.yml` builds the image on
  every PR.

## Open items deferred

- `aarch64` build is exercised in release CI via buildx/QEMU, not on
  this dev machine — first real install on the HA box validates it.
- Add-on `options:`/`schema:` for user-facing config lands with the
  first setting that genuinely needs operator input.
