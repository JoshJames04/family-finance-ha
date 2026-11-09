# GitHub CI/CD → Home Assistant: getting releases live

**Status:** Live — the full push-to-main → HA-update path is
implemented; remaining gap rows are hardening options.

## 1. What exists today

| Stage | Workflow | Trigger | Does |
|---|---|---|---|
| Verify | `tests.yml` | every PR | backend tests + lint + mypy, migration round-trip, dependency vulnerability gates, frontend typecheck/test/build, Playwright e2e, add-on image build + healthz smoke, HA-integration job, repo hygiene (forbidden files, secret scan, docs integrity, version consistency) |
| Publish | `release.yml` | `v*` tag | re-verifies version consistency → container smoke test (healthz + SPA + `/api/v1/version` matches tag) → multi-arch (amd64/aarch64) image to `ghcr.io/joshjames04/family-finance-ha` tagged `:X.Y.Z` + `:latest` → GitHub release with generated notes |
| Distribute | `repository.yaml` + `addon/config.yaml` | — | HA add-on repository descriptor; Supervisor reads `addon/config.yaml`, pulls the prebuilt `ghcr.io` image, installs/updates the add-on. `image:` is pinned to the repo's GHCR namespace |
| Version lockstep | `scripts/version.py` | manual + CI gate | `backend/app/__init__.py::__version__` is the single source; `version.py check` fails CI if `pyproject.toml`, `package.json`, `manifest.json` or `addon/config.yaml` drift |

## 2. The end-to-end path

```
feature PR ──► develop ──► release PR develop→main ──► tag vX.Y.Z
     │              │              │                    │
  tests.yml    merge commit    owner gate          release.yml
  (all gates)                  (approve+merge)     verify→smoke→publish
                                                          │
                                            ghcr.io image :X.Y.Z :latest
                                                          │
                                    HA Supervisor: add-on store refresh
                                    sees new version in config.yaml
                                                          │
                                    user clicks Update (or auto-update)
                                    add-on restarts on new image
                                                          │
                                    Alembic auto-migrates at startup
                                    (downgrade path tested in CI)
```

### Step-by-step release

1. **Accumulate on `develop`.** PRs merge bottom-up; every PR runs the
   full `tests.yml` matrix. `develop` stays releasable.
2. **Cut the version.** On `develop`:
   `python scripts/version.py set X.Y.Z` → commit "Release X.Y.Z" —
   bumps `__init__.py`, `pyproject.toml`, `package.json`,
   `manifest.json`, `addon/config.yaml` together.
3. **Release PR `develop → main`.** `main` is release-only; this PR is
   the approval gate (organisation rules — owner authorises). Body
   keeps `Closes #N` for issues that close on release.
4. **Tag (automatic).** `tag-on-main.yml` runs on every push to
   `main`: it reads `scripts/version.py get`, verifies consistency,
   and pushes `v{version}` on the merge commit if the tag does not
   already exist → fires `release.yml`.
5. **Publish.** CI builds smoke-tested multi-arch image, pushes to
   GHCR, creates the GitHub release.
6. **HA update.** Supervisor's add-on store refresh sees
   `addon/config.yaml` bumped → "Update" appears on the add-on →
   Supervisor pulls `ghcr.io/...:latest` (which now equals X.Y.Z).
7. **Migrate.** The add-on's startup runs Alembic to head; the
   downgrade path is exercised in CI so a bad update can roll back to
   the previous image.

## 3. What needs deciding / building

| Gap | Decision needed | Recommendation |
|---|---|---|
| Who tags | Owner-only vs CI-on-merge-to-main | **Decided: CI-on-merge-to-main.** `tag-on-main.yml` tags `v{version}` automatically; the owner gate is the release PR merge itself. Tag push fires `release.yml`; re-pushes are no-ops while the tag exists |
| Update channel | Manual Supervisor update vs auto-update | Recommend manual: Supervisor shows Update when `config.yaml` version changes; the user applies it at a chosen moment. HA auto-update is fine to enable later |
| `:latest` tag | Update detection depends on version field, not the tag itself — Supervisor compares `config.yaml` version to installed version | Keep `:latest` for `main` releases only; never push `:latest` from develop builds |
| Dev images | No `develop` tag published today | Optional: tag `v*-dev` builds as `:edge` for pre-release testing in a second add-on install; keep off the release path |
| Notification of release | The user's HA knows there's an update via Supervisor's native update entity | No work needed — `update.family_finance` entity surfaces it; an automation could notify on it if wanted |
| Rollback | Reinstall previous image | Documented in RELEASE.md; migrations tested downgrade→upgrade in CI. Keep `backup_include` defaults so HA backups cover the data dir |

## 4. Guardrails already in place

- **Public repo, private data:** `forbidden-files` and `secret-scan`
  gates run on every PR — no workbook, payslip or credential can ride
  into a release artifact.
- **Image carries no secrets:** the add-on reads config/data from the
  mapped volume; vault refs resolve at runtime, not build time.
- **Version consistency is a hard gate:** tag != `__version__` fails
  release before any image is pushed — HA can't be offered a mislabeled
  update.
- **Smoke before publish:** the exact image HA will pull must pass
  `/healthz`, serve the SPA, and report the tag's version via
  `/api/v1/version`.

## 5. Minimal viable loop (what "live" means tomorrow)

The pipeline is sufficient to ship: the only manual acts are
`version.py set` on `develop` and merging the release PR to `main` —
`tag-on-main` then tags `v{version}`, `release.yml` publishes, and
the Supervisor offers the update. Two deliberate steps between
"code on develop" and "Update available in Home Assistant".
