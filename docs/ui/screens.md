# Screen inventory — spike #120 deliverable

Wireframe-level descriptions. Each screen lists: purpose, primary
content, key interactions, API surface, and acceptance hooks for its
implementation issue. Route paths are app-relative (ingress adds the
prefix; see spike #18).

## Now

### `/` Dashboard

Position at a glance. Cards: month net position, days-to-payday,
spend-vs-plan sparkline, alert stack (anomalies / sync failures /
overdue commitments / pending transfers). Quick-action row: add
overtime, add transaction, review transfers.
APIs: `/api/v1/analytics/summary`, `/api/v1/anomalies`,
`/api/v1/sync/accounts/*/cursor`, `/api/v1/transfers`.
Hooks: alert deep-links resolve; pre-migration renders onboarding;
member sees only their-scoped aggregates.

### `/upcoming` Upcoming

Next-30-days list: expected income, commitments due, subscription
renewals, seasonal items. Grouped by week; each row tappable to its
source record.
APIs: `/api/v1/expected`, `/api/v1/commitments`,
`/api/v1/subscriptions`, `/api/v1/seasonal`.
Hooks: past-due items pinned top with overdue styling; amount
provenance (manual vs detected) visible on row.

### `/transactions` Transactions

Register-style list with filter rail: uncategorised / search /
account / date range / possible-duplicate. Row: date, payee,
category chip, amount, account badge.
APIs: `/api/v1/transactions`, `/api/v1/categorisation`,
`/api/v1/reconcile`.
Hooks: inline category edit; bulk-categorise; duplicate-merge
review drawer; private accounts filtered server-side.

## Plan

### `/budget` Budget

Month grid: category rows (budgeted | spent | remaining | pacing
bar), household total header, "left to allocate" cue. Quick-fill:
copy last month, apply forecast, round-up.
APIs: `/api/v1/budgets`, `/api/v1/analytics/pacing`.
Hooks: dirty-guard on month switch; shared/private categories per
scope; autosave row edits.

### `/forecast` Forecast

Projected balance lines per account over 30/60/90d, confidence
band, payday markers, overdraft-risk flags. Scenario drawer
(what-if edits, explicit apply).
APIs: `/api/v1/scenarios`, `/api/v1/analytics/forecast`.
Hooks: every point drill-down to contributors; scenario never
mutates baseline silently; dip warnings link to transfer planner.

### `/goals` Goals & loans

Savings goals (target/date/progress) + loans (balance, schedule,
payoff projection) in one grouped view.
APIs: `/api/v1/goals`, `/api/v1/loans`.
Hooks: goal contribution links to matching transaction; payoff
date updates on extra-payment entry.

## Money

### `/accounts` Accounts

Account list grouped by owner-scope (shared / mine), balances +
last-synced stamp; account detail → its transactions + balance
history sparkline.
APIs: `/api/v1/accounts`.
Hooks: balance always stamped with as-of time; reconciliation state
(matched/unmatched counts) per account.

### `/transfers` Transfers

Pending/scheduled internal transfers (rules + one-offs):
from→to card, amount, date, generating rule. Confirm/skip/edit.
APIs: `/api/v1/transfers`, `/api/v1/transfer-rules`.
Hooks: idempotent confirm; rule traceability; feed-match badge when
both legs reconciled.

### `/subscriptions` Subscriptions

Detected + manual subscriptions: cadence, next charge, annualised
cost, price-change alerts, seasonal service list.
APIs: `/api/v1/subscriptions`, `/api/v1/seasonal`.
Hooks: detection confidence shown; annualised total is a
first-class figure (workbook parity).

## Household

### `/household` Members

Profile list: role, scope, last-active. Admin: role change,
deactivate/reactivate (last-admin guard).
APIs: `/api/v1/profiles`, `/api/v1/households`.
Hooks: role changes audit-stamped; deactivate warns about owned
private data first.

### `/notifications` Notifications & rules

Delivery log (channel, status, sent-at) + rule list
(event → members → channel → quiet hours → privacy mode).
APIs: `/api/v1/notifications`, `/api/v1/notifications/deliveries`.
Hooks: failed deliveries surfaced with cause; lock-screen-safe
preview shown next to privacy toggle.

## System

### `/settings` Settings

Profile prefs, bank connections (token via vault — write-only),
backup/restore, import (Excel staging), household settings (admin).
APIs: `/api/v1/credentials`, `/api/v1/banks`,
`/api/v1/admin/backups`, migration endpoints.
Hooks: token fields never echo values; connection health
(connected/error/expired) per row.

### `/diagnostics` Diagnostics (admin)

Sanitised bundle viewer + copy/download: versions, DB/migration
state, sync status, failure causes, counts.
APIs: `/api/v1/diagnostics`.
Hooks: no amounts/payees/identifiers renderable (server strips);
"copy for support" produces the same sanitised payload.

## Missing-soon

- `/onboarding` — first-run wizard (household → members → Excel
  import → bank connect). Gates Dashboard until dismissed.
- `/employments` — salary records, overtime entry, payslip check
  (J3's home screen once Epic 8 UI builds).
