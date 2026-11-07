# Spike #64: Starling API personal-access capabilities

Status: complete (docs-based; live-token exercise deferred — no token
may enter repo/CI, and none was available locally)
Date: 2026-11-07
Sources: developer.starlingbank.com /docs, /permissions, /faq; HA
core issue #73225 (real-world 429 incident); public SDK schemas.

## Findings

### Tokens

- Personal access tokens (PATs) are created in the Developer Portal
  against the holder's own account. **They do not expire** — no OAuth
  flow, no refresh token. Lifecycle reality:
  - *Rotation* is manual: new token in the portal → update the stored
    credential. Our vault `store` upsert-by-ref covers this.
  - *Revocation* happens in the Starling app/portal; API calls then
    fail 401 → surface as failed sync with a re-authenticate action.
  - Starling prohibits *asking customers for their PAT* — that clause
    targets third-party hosted apps. A self-hosted instance holding
    the owner's own PAT for their own account is the documented use
    case; we still treat the token as write-only input (issue #30).
- Available PAT scopes include far more than reads — PATs *can*
  initiate payments to existing payees. ADR-0008 forbids payment
  initiation; the app must hold exactly:
  `account:read`, `balance:read`, `transaction:read`.

### Rate limits (hard constraint)

- **5 requests/second and 1,000 requests/day** across all endpoints,
  per PAT. HTTP 429 + `Retry-After` header.
- Precedent: HA core integration died to 429s in 2022 by polling too
  often. Design consequences:
  - Poll on a modest schedule — hourly feed sync = ~24 calls/day per
    account, plus discovery/balance calls; far under the daily cap.
  - Honour `Retry-After`; treat 429 as resumable failure (sync
    engine's `RateLimited` already models this).
  - Never let interactive refresh bypass the budget — manual refresh
    queues during backoff (issue #70).
  - `changesSince` far in the past is slow per Starling's own docs —
    bootstrap historically in windows, then stay incremental.

### Feed endpoints and cursor design

Account discovery: `GET /api/v2/accounts` → `accountUid` +
`defaultCategory` (the primary transaction category).
Balance: `GET /api/v2/accounts/{aUid}/balance` → `clearedBalance` /
`effectiveBalance` (minor units).

Incremental feed — the sync primitive:

```
GET /api/v2/feed/account/{accountUid}/category/{categoryUid}
    ?changesSince=<ISO-8601 timestamp>
```

Returns items **created or updated** since the timestamp. This is a
changes-feed, not a page-feed — the sync cursor is the `changesSince`
timestamp itself (`SyncCursor.last_ref` carries it). Historical
backfill uses `settled-transactions-between` and
`paginated-transactions` windows.

Feed-item fields that map onto our model:

- `feedItemUid` → `Transaction.external_ref` (stable, unique)
- `direction` (`IN`|`OUT`) → sign (amount is unsigned minor units;
  `IN` positive, `OUT` negative — matches our ledger convention)
- `amount.minorUnits` → `amount_pence`
- `counterPartyName` → `payee`
- `transactionTime` → `date`
- `status` → our `TransactionStatus`: `SETTLED`/`REFUNDED` →
  `cleared`; `PENDING`/`UPCOMING`/`RETRYING` → `pending`;
  `REVERSED`/`DECLINED` → `void`
- `spendingCategory` → suggestion input for #68's rules
- `updatedAt` → change detection (below)

**Design update for #67:** the ingest path currently treats an
exact-ref hit as a pure dedup skip. A changes-feed returns an item
*again* when it mutates (PENDING → SETTLED is the common case). The
Starling adapter must therefore dedup-or-update: on exact-ref
collision, refresh the mutable fields (`status`, `settlementTime`)
instead of skipping. Exact dedup stays correct for byte-identical
re-fetches; the adapter layer owns the update semantics since the
domain engine is deliberately bank-agnostic. (#66 wires this; no
model change needed — `external_ref` uniqueness is unaffected.)

### Sandbox

`developer-sandbox.starlingbank.com` provides dummy-data accounts
against the real API surface — integration tests for #65/#66 can run
there without credentials of any real account.

## Feasibility verdict

All planned capabilities are feasible with a personal token:
discovery (account:read), balances (balance:read), incremental sync
(transaction:read + `changesSince`). No requirement is infeasible;
the only narrowing is that sync cadence is budget-bound (1,000
req/day), which an hourly schedule respects with large headroom.
