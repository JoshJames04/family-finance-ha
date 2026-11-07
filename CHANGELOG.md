# Changelog

All notable changes to Family Finance are documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versioning follows [Semantic Versioning](https://semver.org/).

`backend/app/__init__.py` is the single version source of truth;
`scripts/version.py check` enforces consistency across pyproject,
frontend, HA manifest and the add-on config. Tags `v*` trigger the
release pipeline (multi-arch GHCR image + GitHub release).

## [Unreleased]

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
