# ADR-0007: Budget Value Model (Original / Revised / Actual)

Status: Proposed
Date: 2026-11-06

## Context

The workbook conflated budgets, forecasts and actuals in single cells —
forecasts got overwritten and history was lost (defect E). The product
requires five values per line: original, revised, actual, variance, accuracy.

## Options considered

- **Triple-value budget lines (chosen):** `original` (immutable after
  approval/lock), `revised` (living forecast, audit-trailed), `actual`
  (derived from transactions/import); variance + accuracy are computed, not
  stored.
- Single value + separate forecast module: reintroduces the workbook's
  conflation under a different name.
- Full version history of every line: rich but heavy; audit trail on
  `revised` gives the needed traceability.

## Decision

Budget lines carry the triple-value model; period locking freezes
`original`; `revised` changes are audited with reason codes; accuracy metrics
(original-vs-actual, revised-vs-actual) power forecasting feedback.

## Consequences

- UI can honestly show "what we planned" vs "what we now expect" vs "what
  happened" — the workbook's core weakness is structurally eliminated.
- Migration maps workbook cells to the honest classification (see defect E).

## Validation / revisit triggers

Revisit only if users demand arbitrary forecast snapshots per line (would add
a snapshot table, not change the model).
