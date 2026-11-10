# ADR-0009: Home Assistant automation v2 — durable outbox + entity-first triggers

Status: Proposed
Date: 2026-11-19

## Context

Issue #432: the eight fixed `family_finance_*` device triggers (#424)
cannot express item-specific selection, thresholds, or rich payloads.
Upstream HA guidance is entity-first; new device-automation domains
are not accepted. The current event source (`events.py`) diffs
aggregate `/summary` counts between polls — it cannot identify *which*
bill/transaction changed, cannot represent paid/overdue transitions,
and guesses where counts move for other reasons.

Requirements driving the design:

- Visual-editor-only UX: All / one / several named items; thresholds
  (bill due in N days, balance below £X, transaction over £X, goal at
  N%); fire exactly once per occurrence.
- Rich, versioned payloads (`bill_name`, `amount_pence`/`amount_gbp`,
  `due_date`, ids) — opt-in only, because the HA event bus, recorder,
  traces and notification providers retain them.
- Reliable delivery: no duplicates across polls, reloads, webhook
  replays or resyncs; no events derived from aggregate guessing.

## Decision

**1. Backend durable event outbox — the single authoritative source.**

New `integration_event` table; rows are appended in the same DB
transaction as the state change that caused them. `id` is the
monotonic cursor. Columns: `household_id`, `event_type`
(dotted, e.g. `movement.settled`), `entity_kind`, `entity_id` (opaque
stable record id — never bank references), `occurred_at`, `payload`
(JSON, schema v1), `dedup_key` (nullable UNIQUE — writers that must
fire once per entity set e.g. `movement.overdue:{id}`; replays hit
the constraint and no-op).

Feed: `GET /api/v1/integration/events?after=<seq>&limit=N`, household-
scoped, ordered by `id`, returns `{events, next_cursor}`. HA polls it
each coordinator cycle. First connect starts at the latest seq (no
replay storm); cursor persisted on the config entry survives reloads
and drives catch-up after downtime.

Payload detail is **redacted at read time** by the persisted app
setting `ha_event_detail` (`safe` default | `detailed`, admin-only
PATCH via the settings API, audited): `safe` keeps type/kind/ids/
dates/status; `detailed` adds names, payees and pence+GBP amounts.
Nothing sensitive ever crosses the API boundary in safe mode; the
toggle takes effect immediately and is reversible.

**2. Entity-first HA side — two native entity kinds.**

- `event.family_finance_<family>` (`EventEntity`, one per family:
  bills, banking, budgets, subscriptions, goals, ops). Each drained
  outbox event fires the matching entity with the payload —
  `trigger.event.data.<field>` templating works natively; users add
  visual conditions (e.g. `amount_pence > 5000`) for thresholds.
- Per-item numeric sensors for continuous values —
  `sensor.ff_bill_<id>_days_to_due`, `sensor.ff_account_<id>_balance`,
  `sensor.ff_goal_<id>_percent` — fed by a bounded per-item list
  endpoint. Native `numeric_state` triggers give above/below/for in
  the visual editor with zero custom code. Entity set is opt-in
  (options flow: All / selected items) and capped; unique ids use the
  stable opaque record id so renames never break automations.

**3. Compatibility.** `events.py`, `device_trigger.py` and all eight
`family_finance_*` names stay verbatim — existing automations keep
working. The outbox feed is additive.

**4. Emission discipline.** Events are written only at committed
canonical transitions: `post_sync` settlement (`movement.settled`,
`movement.overdue` first-transition via dedup_key), feed ingest
(`transaction.imported`), subscription price observation
(`subscription.price_observed`), sync run terminal state
(`sync.failed` / `sync.recovered`). No event is derived from count
movement; none is advertised before its backend transition exists —
bill-paid events wait on #429, budget thresholds on #415.

## Consequences

- Reliable, replayable, testable event stream; HA side stays a thin
  consumer — no financial logic in the integration.
- Per-transaction ingest writes one row per feed item — bounded by
  feed page sizes; a retention sweep (e.g. keep 30 days) follows if
  volume demands.
- Staged rollout: this ADR + backend outbox first; HA event entities
  and per-item sensors land under child issues; remaining families
  (budgets, payroll, gifts, energy, ops) extend the same feed.
- Privacy: safe mode is the default and server-enforced; detailed
  mode documents that HA visibility is household-wide.
