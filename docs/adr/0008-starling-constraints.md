# ADR-0008: Starling Integration Constraints

Status: Proposed
Date: 2026-11-06

## Context

Read-only Starling sync is required — but personal access tokens have
scope/rate/refresh constraints, and the app must never depend on the
integration to function.

## Options considered

- **Read-only, least-privilege, optional (chosen):** personal access token in
  the encrypted vault; sync is an adapter feeding the same transaction model
  as manual entry; dedup via external refs; categorisation is suggestive.
- True-banking/Open-Banking aggregation: regulated third-party dependency;
  out of scope.
- Any payment initiation: explicitly forbidden without a new approved
  requirement — the client must never hold payment scopes.

## Decision

Starling adapter: account discovery, balances, incremental transaction sync,
matching + reconciliation against internal records. Manual accounts are
first-class; sync failure degrades to manual, surfaced in diagnostics and
sensors.

## Consequences

- Token lifecycle (rotation, revocation, expiry) is a designed feature, not
  an afterthought.
- Spike early in M5 validates actual personal-access capabilities; findings
  may narrow scope (documented, not hacked around).

## Validation / revisit triggers

Spike outcome; Starling API deprecations; user demand for additional
institutions (would generalise the adapter interface).
