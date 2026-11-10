# Spike #417: Starling webhooks for near-real-time updates

Status: complete (documentation-based; no live endpoint exercised —
wiring one requires the owner's developer-portal session)
Date: 2026-11-10
Sources: developer.starlingbank.com/docs (V2 webhooks section),
/permissions; developer.starlingbank.com/payments/docs (retry/signature
detail); public `starling-webhooks` type crates for payload shape.

## Findings

### What officially exists

- **V2 webhooks are available to personal access tokens.** Developer
  Portal → Personal Access page → "Personal Access V2 Webhooks" →
  Create Webhook (name, payload URL, event selection). This is the
  documented path for notifying "your own account" — it is not
  business-integration-only.
- **Event surface:** Feed Item (card transactions, cash withdrawals,
  direct debit payments), Standing Order (create/edit/delete),
  Standing Order Payment. Nothing else: **no balance-change,
  space-transfer, or account-state events** — those still need the
  REST endpoints.
- **V2 payload is self-contained** (`WebhookDispatchFeedItem` —
  feed item fields + `feedItemUid` + timestamps) — no follow-up API
  call required to learn what happened, though a pull is still
  needed for balances.
- **Authentication:** V2 signs each request with a private key; the
  portal gives the holder the **public key** to verify with. (V1's
  shared-secret `X-Hook-Signature` HMAC-SHA512 scheme still exists
  for the payments API; V2 public-key verification is preferred.)
- **Scopes:** events are greyed out unless the token holds the
  matching scope — `transaction:read` suffices for feed items.
- **Retries:** payments docs specify 2 s response timeout then
  exponential redelivery over ~2 h; the V2 feed-item path documents
  the same "2XX or retry" contract. Ordering is not guaranteed —
  replay protection must treat deliveries as at-least-once.

### The reachability constraint

A webhook needs a **public HTTPS callback**. A Home Assistant add-on
behind NAT/firewall has no public route — and its ingress URL must
never be exposed. Options ranked by safety:

| Approach | Freshness | Requirements | Verdict |
|---|---|---|---|
| Current polling (4 h) | ≤4 h | none | default |
| Shorter polling (30–60 min) | ≤1 h | none; still ~48 calls/day/account, far under the 1,000/day cap | recommended default bump; configurable |
| Webhook via tunnel/proxy | seconds | user-operated public endpoint (Cloudflare Tunnel, reverse proxy) terminating at the app | opt-in design below |
| Relay service | seconds | third-party relay sees transaction metadata | reject — privacy |

### Recommended design (opt-in, feasible)

If and only if the operator exposes the app publicly, a minimal
webhook receiver is cheap **because it reuses the existing sync
machinery**:

- `POST /api/v1/integrations/starling/webhook` — registered only
  when `FF_STARLING_WEBHOOK_PUBLIC_KEY` is configured; otherwise the
  route does not exist (inert by default, no attack surface).
- Verify the V2 signature against the configured public key over the
  raw body; reject with 401 on mismatch. Constant-time compare.
- On a valid Feed Item event, call `sync_domain.request_refresh()`
  for the mapped account — the same queue a manual Sync press uses.
  The scheduler drains it on the next tick through the normal
  `sync_account` pipeline: dedup (`feedItemUid` → `external_ref`,
  exact-ref skip), `RateLimited` backoff, cursor advance. No new
  transaction-write path.
- Balances refresh on the same drain (the #416 scheduled parity
  already couples discovery+balances to due work).
- Idempotent by construction: replayed/reordered events merely
  re-queue a refresh; fetch-side dedup handles item replays.
- Polling stays enabled as the fallback — webhooks accelerate, they
  never replace the scheduled cadence (missed/expired subscription
  degrades gracefully to the status quo).

### Out of scope / rejected

- Exposing the HA instance: the deployment guide must tell operators
  to terminate TLS at their own proxy and restrict the route to
  Starling source IPs where their proxy supports it — the app cannot
  enforce reachability.
- Standing-order events: no value for sync (already covered by feed
  items); skip in the child implementation.

## Recommendation

1. **Keep polling as the supported default.** The interval is
   already configurable via `FF_SYNC_INTERVAL_MINUTES` (default
   240). Operators wanting fresher data can lower it — floor ~30 min
   stays far inside the 1,000 calls/day budget (spike #64).
2. **Create one child issue** for the opt-in webhook receiver above —
   feasible, small, and gated entirely behind explicit operator
   configuration.
3. No public endpoint is exposed by this spike; no credentials used.
