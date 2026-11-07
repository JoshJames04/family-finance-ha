# User journeys — spike #120 deliverable

Status: complete (approval gate for UI feature builds)
Scope: mobile-first, single-household, two-role (admin/member).
Conventions borrowed from Actual Budget (envelope-style monthly
views, account register) and Grocy (household-first framing) —
patterns, not layouts.

## Navigation model

Sidebar (desktop) / bottom bar (mobile), one level deep — no nested
menus. Grouped by *frequency of use*:

| Group | Items |
|---|---|
| Now | Dashboard · Upcoming · Transactions |
| Plan | Budget · Forecast · Goals |
| Money | Accounts · Transfers · Subscriptions |
| Household | Members · Notifications |
| System | Settings · Diagnostics (admin only) |

Rationale: the daily act is *checking position* (dashboard/upcoming)
and *triaging transactions*. Planning screens are one tap deeper —
they're weekly/monthly acts. System screens are deliberately last.

## Primary journeys

### J1 — Daily check-in (highest frequency)

`Dashboard` → glance: net position this month, days-to-payday,
spend-vs-budget sparkline, unread alerts (anomalies, sync failures,
overdue commitments). One tap from alert → its source screen
(anomaly detail, sync status, commitment).

Acceptance hooks:
- Loads meaningful state with zero interaction (no "pick a month")
- Every alert card deep-links to a resolvable screen, never a modal
  dead-end
- Empty household (pre-migration) renders onboarding, not zeros

### J2 — Transaction review & categorise

`Transactions` → filter rail (uncategorised default when count > 0)
→ inline category picker per row → bulk-select for repeated payees
→ rule suggestion ("always categorise X as Y") on repeat patterns.

Acceptance hooks:
- Uncategorised count is a first-class filter chip, not a dropdown
- Category change is one tap; no separate edit screen on mobile
- Private-scoped accounts only appear for their owner (per #32)
- Merged duplicate candidates surface as "possible duplicate" badge,
  not silent removal (per #66/#114)

### J3 — Overtime entry (weekly, seconds not minutes)

`Dashboard` quick-action or `Employment` → current pay period
pre-filled → enter hours (stepper, decimal-friendly) → confirms
estimated £ impact using last-known rates → done. Editing a
submitted period is a correction flow, never silent overwrite.

Acceptance hooks:
- ≤3 taps from open to saved on mobile
- Rate resolution is visible ("at £14.20/h × 1.5") — trust
  requires showing the multiplier used
- Payslip-relevant note field optional, not required

### J4 — Monthly budget setup

`Budget` → month switcher → per-category rows: budgeted, spent,
remaining, pacing bar → quick-fill affordances (copy last month,
apply forecast, round to £5) → household total header shows
under/over-allocation live.

Acceptance hooks:
- Month navigation never loses unsaved row edits (autosave or
  dirty-guard)
- Shared vs private categories respected per #32 scoping
- Zero-based cue: "£X left to allocate" until fully budgeted

### J5 — Forecast check

`Forecast` → horizon selector (30/60/90d) → projected balance line
per account with confidence band → dips below £0 flagged as
overdraft risk (links to #75 warnings) → "what-if" scenario toggle
compares against baseline without mutating it.

Acceptance hooks:
- Every projected figure is traceable: tap → contributing
  commitments/expected items list
- Payday is marked on the timeline (per #7 payday intelligence)
- Scenario edits never write to real expected items until
  "apply scenario" is explicit

### J6 — Transfer review

`Transfers` → pending internal transfers list (payday rules,
savings sweeps, household contributions) → each card: from→to,
amount, scheduled date, rule that generated it → confirm /
skip / edit amount.

Acceptance hooks:
- Auto-generated transfers show their originating rule and can be
  traced to it
- Confirming is idempotent — double-tap can't double-post
- Failed/rejected bank feed matches surface on the same card

## Secondary journeys (screen inventory covers)

- J7 onboarding/migration import (Excel → staged review → commit)
- J8 bank connection setup (token via vault → discover → link)
- J9 household member management (admin: roles, deactivate)
- J10 notification rule authoring (channel, quiet-hours, privacy)

## Cross-cutting design rules

- **Relative everything** — the app lives under an ingress path
  prefix (spike #18); no absolute asset or route URLs.
- **Money display**: `£1,234.56`, negatives as `-£x` not red-only
  (colour-blind safe); minor units everywhere until render.
- **Privacy**: lock-screen-safe notification wording (#119); member
  scoping is server-enforced — UI never relies on hiding alone.
- **Offline honesty**: read-model loads are stale-tolerant with a
  "last updated" stamp; writes require connectivity and fail loud.
