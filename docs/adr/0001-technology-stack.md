# ADR-0001: Technology Stack

Status: Proposed
Date: 2026-11-06

## Context

Self-hosted HA app, single maintainer, decades-long lifespan, financial
correctness over novelty. Proposed: React+TS / FastAPI / SQLite.

## Options considered

- **React+TS / FastAPI / SQLite (chosen):** mainstream, typed both ends,
  OpenAPI client generation, SQLite's zero-ops fits HAOS volumes.
- Full-stack frameworks (Next.js, Django): heavier ops; HA ingress favours
  a static SPA + API.
- Actual Budget-style Node backend: weaker fit for payroll/tax domain
  libraries and typed data work than Python.
- PostgreSQL: stronger concurrency, but adds a second service on HAOS and
  complicates backup/rollback for a single-household workload.

## Decision

Adopt React + TypeScript SPA, FastAPI backend, SQLite persistence (WAL,
single-writer via the backend, Alembic migrations, online `VACUUM INTO`
backups).

## Consequences

- Persistence is isolated behind a repository layer; PostgreSQL remains a
  documented escape hatch, not a planned migration.
- Determinism: domain logic is pure Python, versioned parameter sets — easy
  to test.
- CI must include migration upgrade/downgrade tests and DB integrity checks
  (conditions of this decision).

## Validation / revisit triggers

Revisit if: observed write contention, DB corruption in the field, or a
multi-user write workload emerges.
