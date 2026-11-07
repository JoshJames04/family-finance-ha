# ADR-0008: Starling Integration Constraints

Status: Accepted
Date: 2026-11-06 (validated by spike #64 on 2026-11-07)

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

Spike #64 validated the concrete mechanics (docs/spikes/0064-starling-api.md):

- Personal access tokens do not expire — no OAuth or refresh path; rotation
  is a manual portal action handled by vault upsert; revocation surfaces as
  401 → failed sync with a re-authenticate prompt.
- Held scopes are exactly `account:read`, `balance:read`,
  `transaction:read` — PATs can hold payment scopes; we never request them.
- Rate budget is 5 req/s and 1,000 req/day — hourly scheduled sync leaves
  large headroom; 429 + `Retry-After` maps to the resumable-failure path;
  manual refresh queues during backoff rather than bypassing the budget.
- The feed is a changes-feed: `GET /feed/account/{a}/category/{c}
  ?changesSince=<ts>` returns items created *or updated* since the cursor.
  The sync cursor is that timestamp; `feedItemUid` is the stable
  `external_ref`. Exact-ref collisions update mutable fields (status,
  settlement time) rather than dedup-skip — the PENDING → SETTLED
  transition depends on it. Historical backfill uses the
  `settled-transactions-between` / `paginated-transactions` windows, then
  the cursor takes over.
- Starling's sandbox (developer-sandbox.starlingbank.com) allows
  integration testing without real credentials.

## Consequences

- Token lifecycle (rotation, revocation, expiry) is a designed feature, not
  an afterthought — PAT non-expiry simplifies it to rotation-on-demand.
- The sync budget (1,000 req/day) caps cadence and is owned by the
  scheduler, not individual calls.
- Dedup-or-update refines the sync engine's exact-dedup path at the
  adapter layer only; the bank-agnostic substrate is unchanged.

## Validation / revisit triggers

First live-token sync in #65/#66; Starling API deprecations; user demand
for additional institutions (would generalise the adapter interface).
