# ADR-0006: Excel Migration Provenance & Integrity

Status: Proposed
Date: 2026-11-06

## Context

The workbook is imperfect (documented defects A–F) yet is the only source of
2024+ history. Import must be trustworthy, repeatable and auditable — and the
workbook must be safely retirable afterwards.

## Options considered

- **Staged pipeline with provenance records (chosen):** parse → staging →
  classify → validate → commit → reconcile. Every imported datum keeps
  sheet/cell provenance + classification; batches are idempotent,
  dry-runnable and rollback-able.
- One-shot transform script: fast but irreproducible and unauditable.
- Re-entry by hand: impractical volume.

## Decision

As per [MIGRATION.md](../MIGRATION.md). Key invariants: source opened
read-only + hashed; aggregates stored as aggregates (never synthesised into
transactions); overwritten forecasts classified, never claimed original;
overlap with bank-synced data cannot double-count; unresolved items go to a
review queue, never silently fixed.

## Consequences

- Import is a product feature (rerun-able), not a one-off script — larger
  initial cost, permanent audit value.
- Workbook retirement is gated on the reconciliation report, not a date.

## Validation / revisit triggers

None anticipated; retirement gate is explicit.
