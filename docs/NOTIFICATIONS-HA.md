# Home Assistant automations and energy statistics

The integration polls the app every minute. It exposes summary sensors, eight
compatible device triggers, durable per-family event entities, and optional numeric
sensors for individual Bills, Accounts, Goals, Budgets, Seasonal budgets and
authorised payday occurrences. The app remains authoritative for
financial calculations and committed transitions.

## Connection and privacy

Configure the app URL, household ID and the app's service token. The integration
sends the token as `X-Admin-Token`; it never invents a household member or an admin
profile. A rejected token enters HA reauthentication. The token grants only the
server's explicit service allowlist and shared resource scope. Existing HA actions
for adding a shared transaction or recording permitted overtime remain audited by
the app; unauthorised private resources are rejected.

Safe event detail is the app default. An app administrator can select **Safe** or
**Detailed** in Settings. Detailed mode can place names and monetary values in the
HA event bus, entity attributes, recorder, automation traces and notification
recipients. Anyone who can access those surfaces may see that detail. HA is not a
per-member private financial store. Switch back to Safe to redact subsequent feed
reads; this does not erase old HA history, traces or notifications.

Per-item numeric sensors have a separate explicit opt-in. In **Settings > Devices
& services > Family Finance > Configure**, choose None, All, or Selected for each
family. Selected is a multiple-item picker with names; its stored identity is the
opaque record ID, so renaming an item preserves automations. None is the default.
The global cap is configurable from 1 to 100 entities. A choice exceeding the cap
is rejected; new items cannot silently grow the active entity set beyond it.
Account sensors expose GBP balances, Bill sensors expose days to due, and Goal
sensors expose percent. Summary monetary sensors remain disabled by default in the
entity registry. Consider recorder exclusions before enabling financial entities.

Additional per-item metrics use the same None / All / Selected controls and the
same global cap; each enabled metric counts as one entity. Existing Bill, Account
and Goal sensor identities stay unchanged. The new metrics are:

| Metric | Canonical value | Unit |
| --- | --- | --- |
| Safe to spend | Account cleared-ledger balance minus outstanding obligations until the next expected payday, or the configured horizon when none exists | GBP |
| Lowest projected balance | Minimum of the account's current balance and canonical daily projection over the configured horizon | GBP |
| Budget remaining | Approved/locked expense plan minus signed, split-aware, as-of actual spending in that budget's owner scope | GBP |
| Budget usage | The same actual spending divided by the positive expense plan; unknown when the plan is zero | % |
| Seasonal remaining | Event budget minus recorded gift actuals, keeping planned commitments separate | GBP |
| Seasonal usage | Recorded gift actuals divided by a positive event budget; unknown for a zero budget | % |
| Goal funded amount | Real, eligible inbound transfer contributions to the goal, separate from its percentage metric | GBP |
| Days to goal target | Configured target date minus the app calendar date; absent for undated goals | days |
| Days to occasion | Explicit occasion date minus the app calendar date; absent until a real date is configured | days |
| Days to payday | Earliest outstanding positive employment/payroll-run occurrence for each authorised active employment, including today, within two years | days |

Forecasts are estimates, not received income or confirmed future payments. They
use the same canonical calculations as account forecasting. Configure permits a
1–731 day forecast window, defaulting to 90; changing it preserves entity identity.
Budget and
seasonal percentages use two decimal places; zero plans do not fabricate a
percentage. Day counts use the app server's calendar date, matching its scheduling
and forecasting default, rather than the HA browser's timezone. A selected metric
becomes unavailable if its source is deleted, archived or no longer authorised;
renaming preserves its stored identity.

Budget metric labels show their actual reporting dates and basis. Personal
monthly budgets use the same planned pay-cycle boundaries as the personal Budget
view; Shared and nonmonthly budgets retain calendar/explicit dates. Unknown or
ambiguous personal pay-cycle configuration omits these metrics instead of
silently substituting a calendar figure. Archived-account spending remains part
of the appropriate owner's budget history.

Employment ownership applies even when a salary destination account is shared.
The HA service identity has shared scope, so private employment names/payday
metrics remain absent. Detailed event mode does not grant private access. A new
account forecast metric is also omitted if any outstanding payroll input within
its forecast window has private or unresolved provenance for the requesting
identity. This avoids publishing a private-derived figure as a shared zero or
partial estimate. The actual shared account balance sensor remains independent.
Payday numeric discovery is groundwork for authorised app profiles; the current
HA machine service cannot expose a private employment until an explicit resource
grant model is approved under #532. Selecting All does not override that boundary.

Discovery is paginated in stable order, so later metric families are not silently
lost after the first 100 records. Original Accounts, Goal percent and Bills retain
their previous ordering before any new metric is appended, preserving the first
page for older integrations that cannot paginate. HA supports up to 1,000
discoverable metrics and
fails with a discovery error beyond that bound; the separate active-entity cap
remains at most 100. No transaction-by-transaction entities are created.

For a visual threshold automation, enable the desired named metrics in Configure,
then choose one or several in the native Numeric state entity picker. Use Below
`0` for a negative projected balance, Below `100` for GBP 100 safe to spend, or
Above `89.99` for a 90% budget/seasonal usage threshold. For an authorised payday
within N days, use Below `N + 1` and Above `-1` on days-to-payday sensors. These
native controls support arbitrary day/amount/percent thresholds without YAML.
State/Template conditions can express equality or compare a selected event's
stable ID. Native numeric triggers fire on entry into their configured range;
creating an automation while already inside it is not a historical replay.
Actual visual-editor, multi-target and notification-delivery validation remains
under #441.

## Version 1 payload catalogue

The integration publishes `event` entities for bills, banking, budgets,
subscriptions, goals, loans, payroll, gifts, energy and ops. Their unique IDs
contain the config-entry ID and family, not a display name. Canonical events also appear on the
bus as `family_finance_<family>_event` for visual Event triggers.

Every canonical bus payload contains `schema_version: 1`, `seq`, `event_type`,
`entity_kind`, `entity_id`, `occurred_at`, `config_entry_id`, and `household_id`, plus
the server-filtered `data` fields. There are no client-side guessed financial
transitions. Unknown future types in a recognised family remain consumable.

| Family | Canonical transition types / source |
| --- | --- |
| Bills | `movement.settled`, `movement.partial`, `movement.overdue` from settlement/overdue processing; `bill.created`, `bill.paused`, `bill.resumed`, `bill.amount_changed`, `bill.date_changed`, `bill.ended` from committed recurring-commitment changes |
| Banking | `transaction.imported`, `transaction.settled`, `transaction.review_required`, `transaction.reconciled`, `transaction.refund_linked`, `transaction.transfer_linked`, `account.balance_changed`, `connection.revoked`, `connection.disconnected` from canonical banking writers |
| Subscriptions | `subscription.price_observed` from an observed price; `subscription.created`, `subscription.cancelled`, `subscription.amount_changed`, `subscription.renewal_changed` from committed register changes |
| Ops | `sync.failed`, `sync.completed`, `sync.recovered`, `data.stale`; `backup.created` / `backup.failed` from real backup outcomes |
| Budgets | `budget.approved`, `budget.locked`, `budget.usage`, `budget.over`, `budget.remaining_changed` from canonical lifecycle/financial changes |
| Payroll | `overtime.recorded`, `payroll.estimate_changed`, `payroll.generated`, `payroll.reconciled`, `payroll.approaching`, `payslip.review_required`, `payslip.state_changed` |
| Gifts | `gift.purchased`, `gift.paid`, `seasonal.variance`, `seasonal.approaching` |
| Energy | `energy.reading_updated`, `energy.variance` from changed readings / canonical estimated costs |
| Goals | `goal.milestone` / `goal.completed` from real contributions; `goal.behind_plan` from canonical on-track deterioration; `goal.target_approaching` in the default three-day deadline window; per-goal numeric percent supports other visual thresholds |
| Loans | `loan.balance_changed`, `loan.fully_repaid` from the dated outstanding balance; recorded repayment does not imply bank settlement |

A matched payment emits `movement.partial` while an outstanding amount remains.
Only a payment that completes the obligation emits `movement.settled`; replaying
an already matched transaction emits neither event again.

Bill creation, amount/date edits, pause and resume events come from the actual
register mutation. Repeating an unchanged edit, pause or resume emits no duplicate
transition. Scheduler evaluation emits `bill.ended` only after an explicit end
date, or after the final dated occurrence of an exhausted configured count, has
passed. Materialising a future final occurrence does not end a bill early; both
materialisation and normal settlement supply the authoritative occurrence state.
The first evaluation seeds existing records quietly. New bills seed their baseline
inside creation, so an offline gap before their first scheduler wake cannot lose
the ending. A schedule edit that reopens an ended bill resets its baseline in
the same transaction, so a later ending is retained even if HA/app polling misses
the reopened window. Edits that leave it ended do not create another ending.
Subsequent crossings survive restart/downtime and do not repeat. If both end
conditions pass during downtime, the event identifies whichever dated boundary
came first, matching the schedule engine. This is a schedule ending, not proof of
payment; merely passing a due date without a configured end condition does not
emit it. Formula text and notes never enter these payloads.

Subscription creation also covers confirmation of a detected candidate.
`subscription.renewal_changed` describes an explicit renewal date/frequency edit,
or the existing renewal advance after an observed price (`source: price_observation`).
An observation or advanced renewal is not a confirmed settled charge, missed
payment, expiry or completed renewal contract. Cancellation is an explicit register
mutation; the current model has no automatic expiry or reactivation transition.
No duplicate due-reminder polls are added.

All these rows share the writer's transaction: rollback removes the event.
Paused/cancelled records retain their opaque `entity_id`; renaming never changes
that ID. Feed reads apply current account ownership in Safe and Detailed modes.
Archived accounts retain their ownership restriction. If a record is permanently
deleted, its events are hidden because its current visibility can no longer be
verified; deletion does not disclose a tombstone to HA. A transactional internal
delete watermark keeps old bill events hidden even if SQLite reuses its record
ID later; recreating a bill never revives the deleted bill's private history.
New bill lifecycle and subscription lifecycle/price-observation rows also retain
the account owner scope at emission. Making an account shared or assigning it to
another owner cannot reclassify those queued private rows as shared history.
Current ownership must still allow the read; internal scope metadata is stripped
even in Detailed mode. Accountless subscriptions keep their existing shared scope.
Legacy rows without this emission-time binding retain their existing current-scope
behavior; their original ownership cannot be reconstructed and is not rewritten.

A real contribution first reaching a goal's target also emits `goal.completed`
once per goal, alongside the compatible 100-percent milestone. A later contribution,
replayed pairing or no-op poll does not repeat completion. Rollback removes the
contribution and its event together. This does not infer completion from an opening
balance, a target edit, or an existing funded goal at installation. Detailed mode
adds the goal `name`, `amount_pence`, `amount_gbp`, `target_pence` and `currency`;
Safe mode retains only source/status/date/currency. Account ownership applies in
both modes. For a completion notification, use an Event trigger for
`family_finance_goals_event` with `event_type: goal.completed` and the intended
`config_entry_id`; optionally select stable `entity_id` values in a Template
condition. Detailed actions can use `{{ trigger.event.data.name }}` and
`{{ trigger.event.data.amount_gbp }}`. Actual editor and delivery acceptance are
still required under #441.

Completion dates and funded amounts come from the chronological contribution
ledger, even when transfers are linked out of date order. Linking a future-dated
credit seeds current progress without firing early; the durable scheduler emits
completion when that credit becomes effective, with its actual crossing date.
An already funded installation still seeds quietly instead of replaying history.
New goal events retain their original private owner internally, so making the
account shared later cannot disclose an old private notification payload. This
internal scope marker is never included in either Safe or Detailed feed data.

The scheduler also evaluates active goals without requiring a notification rule.
`goal.behind_plan` fires when the canonical on-track calculation changes from true
to false. Initial existing behind-plan data seeds a quiet baseline; an undated
goal is unknown, not behind. Staying behind does not repeat the event; recovering
and later falling behind creates a new transition. A fully funded goal does not
generate a behind-plan warning after its deadline. `goal.target_approaching`
fires once per goal and target date in the default three-calendar-day window,
including the due date. Returning from downtime inside that window catches up;
past deadlines, future-start, inactive, undated and already funded goals do not
produce an approaching event. No historical deadline is replayed after the window.
The app scheduler's calendar date drives the window, with `lead_days: 3` and
`expected_date` in the payload; arbitrary user-configured lead days are not yet
provided by this event. Safe mode keeps status/dates/lead time/currency; Detailed
mode adds the current goal name, funded amount, target and remaining pence. Both
the prior-state snapshot and deadline deduplication persist transactionally across
restarts. These are goal-plan signals, never evidence of a missed bank payment.

Use only types implemented by the installed app version. An available event entity
is a stream endpoint, not evidence that every conceivable transition exists.
Detailed transaction events can include `payee`, `amount_pence`, `account_id` and
`date`; subscription observations can include `payee`, `amount_pence`, `old_pence`
and `date`. Safe mode excludes names and monetary detail server-side.

Banking transitions distinguish provider settlement (`transaction.settled`: a new
explicitly cleared feed row or an actual pending-to-cleared change, including manual
clearing)
from an accepted expected-payment match (`transaction.reconciled`). An ambiguous
persisted reconciliation proposal or fuzzy import-overlap candidate emits
`transaction.review_required`; an import alone proves neither settlement nor
reconciliation. Unknown/missing provider statuses do not emit settlement merely
because legacy ledger ingest defaults a new row to cleared. Replayed provider rows
retain local reconciled/void states and do not produce another settlement event.
Provider overlap candidates with the same account/source/reference retain their
original review object across replay, including after resolution or later edits
to local/provider fields; they do not create duplicate ledger rows or review notifications. Reference-free
feeds cannot prove replay identity and retain a separate candidate per observation.
Refund and transfer events require the actual canonical link; labels and equal
amounts alone are insufficient. Transfers publish one event for each leg using
that transaction's scope, without copying another leg's account or transaction ID.
Existing account/transaction IDs remain stable across provider resync and renames.

`account.balance_changed` compares a known persisted provider balance with its new
observation for a linked account or Space. First observation seeds the baseline;
unchanged refreshes stay quiet, while a later return to an older value is a new
change. Detailed mode includes `old_balance_pence` and `balance_pence`; safe mode
omits them. This is a provider snapshot signal, not fabricated ledger activity.
Standalone balance refresh, discovery, reauthentication and disconnect share the
transaction sync connection owner and commit before releasing it. This includes
Connect reviving an existing revoked row; genuinely new or unrelated connections
remain independent. Overlapping requests return retryable 409 without provider
calls or credential mutations, preventing duplicate/stale-prior balance events
and successful in-flight syncs overwriting a concurrent disconnect.
Connection revocation fires on a persisted 401/403 transition; transient failures
do not imply revocation. Explicit user removal emits `connection.disconnected`.
Repeated removal stays quiet based on revoked state and actual credential removal,
independent of the recorded error message.
Late provider errors cannot revive a persisted revoked connection, whether the
credential remains or was removed, including responses in a stale request session.
Only explicit successful reauthentication reopens the connection lifecycle.
Connection events are separately scoped to each mapped account and contain no
provider identifiers, labels, credentials or error text. An unmapped connection
has no provable member scope, so its event is visible only to an all-scopes admin.
Every hook shares the state writer's transaction: rollback removes its events.
New banking events, including imports, retain their original account ownership as
internal scope metadata. Making a private account shared later cannot disclose
those earlier events; current ownership still applies as an additional restriction.
The internal owner marker is removed in both Safe and Detailed payloads. Events
written before this ownership snapshot existed retain the legacy visibility policy;
historical ownership cannot be reconstructed from the current account alone.

Use the visual Event trigger with `family_finance_banking_event`, select the
desired `event_type`, and optionally match its local `entity_kind` / `entity_id`.
Numeric account entities remain available for visual balance threshold triggers.
Named multi-account/member controls and real visual-editor acceptance remain
tracked separately in #432 / #441.

The eight existing device trigger names remain: bill due, bill overdue, sync
failed, goal milestone, low balance, notification pending, payday and payslip
review. These remain summary transition signals, not item-specific outbox events.
First successful connection/reload seeds the summary baseline without replaying
historical conditions. Each emitted event includes `config_entry_id`, and device
triggers filter it so another configured household cannot activate the device.

### Scoped payroll backend feed; private HA access pending

`overtime.recorded` is emitted when a new draft overtime entry is committed,
including the existing permitted app/HA action paths. It describes recorded hours
and estimated overtime pay, not approval, a payslip or money received. Editing,
submitting or approving that entry does not repeat its creation event. The opaque
`entity_id` identifies its employment; Detailed payloads add the entry ID, hours,
employer name and estimated pence. Hours and pay use the persisted two-decimal
hours and final hourly rate, including higher-precision requests. Notes are never
included.

`payroll.estimate_changed` compares the previous and new canonical expected net
amount for the same employment/pay date/destination during schedule regeneration.
It covers regular employment estimates and forecast payroll runs, using their
configured payroll period and salary policy rather than assuming annual salary
divided by twelve. A first known
value seeds quietly; unchanged regeneration emits nothing. A reconciled/actual
amount is not labelled an estimate, and a missing estimate is never a fabricated
zero. A changed payday/destination is not compared as though it were the same
obligation. Detailed payloads contain old/new estimated pence and employer name;
Safe mode keeps structural dates/status/source only. Rollback removes changes and
events together. Event delivery/retry uses the existing durable cursor.

Both types inherit the employment owner's visibility even when pay lands in a
shared account. They are available through the authenticated, profile-aware backend
feed. **The current HA service token has no private payroll grant and cannot read
either new type**, in Safe or Detailed mode. The integration does not send a member
identity, and it cannot impersonate an employment owner. These writer hooks are
backend groundwork; they do not deliver a private payroll automation to HA.

Admin-selected private read grants require the access-model decision in
#532 (under #524/#432). Rich detail opt-in and named entity selection do not grant private
access. The existing HA adapter can publish an authorised canonical payroll row,
but mocked/harness publication proves that adapter behavior only; real service
HTTP tests prove the current private denial. No private HA automation recipe is
claimed until explicit access is designed, approved and tested. Renaming preserves
the employment ID for authorised backend consumers.

## Cursor, retry and offline behaviour

A new install requests the latest visible event cursor and emits no old history.
A saved `event_cursor` in the config entry survives reload. Later polls drain after
that cursor in order, at most 1000 events per refresh. A failed or malformed drain
leaves the cursor unchanged, allowing retry. No-op/overlapping feed rows do not
refire an entity. Reload catches up on committed events after the saved cursor;
summary baseline suppression does not discard these new durable events.
Fetching a page does not acknowledge it. The cursor is persisted only after
platform setup and successful synchronous publication by enabled coordinator
listeners. Failed publication retains the pending page for retry; a crash before
acknowledgement resumes from the old durable cursor.

HA's event bus and config-entry storage do not share an atomic transaction. A hard
process crash during delivery/storage can replay a sequence at that boundary;
external actions needing crash-level exactly-once behaviour must deduplicate on
`config_entry_id` plus `seq`. Ordinary polls/reloads and failed drain retry are
covered by integration tests. No actual notification or provider delivery is
claimed by those tests.

## Five visual-editor recipes

These are step-by-step recipes for the actual HA editor controls, with synthetic
names and values. They are implementation guidance, not completed live-editor
acceptance evidence. Screenshots and installed HAOS/mobile/ingress validation
remain to be captured during the owner's review. No screenshots are fabricated.

### 1. A selected bill is due in three days

1. In integration Configure, choose Selected Bills and pick one or several bills.
2. Open **Settings > Automations & scenes > Create automation > Create new
   automation**. Under When, add an **Entity > Numeric state** trigger and select
   those bills' days-to-due sensors. Set Below to `4` and Above to `-1`.
3. Under Then do, select the household's notification action and enter a reminder.
4. Save. A crossing into three-or-fewer non-overdue days triggers once; remaining
   within that range does not repeat each minute. An item already inside the
   range when an automation is created needs the editor's Test flow, or a later
   crossing, to exercise it. Choose All Bills in Configure for the bounded All set.

### 2. An account balance drops below GBP 100

1. Opt in to Selected Accounts (or All within the cap).
2. Create a **Numeric state** trigger for the selected account balance sensor(s),
   Below `100`. The units are GBP, so enter pounds rather than pence.
3. Optionally set For to ten minutes to ignore brief crossings. Add a notification
   action and save. Use HA's trace to verify the selected entities and crossing.

### 3. A new imported expense exceeds GBP 50

1. An app administrator enables Detailed event data after reading its warning.
2. Create an **Other triggers > Event** trigger with event type
   `family_finance_banking_event` and event data `event_type: transaction.imported`.
   Add `config_entry_id` for the intended configured household when more than one
   is installed. These are fields in the visual Event form, not YAML editing.
3. Add a **Template** condition in the visual condition editor:
   `{{ trigger.event.data.amount_pence | default(0) | int < -5000 }}`.
   Negative pence denote expenses. Optionally filter the opaque `account_id` for
   one or several authorised accounts.
4. In a notification action's message field use
   `{{ trigger.event.data.payee }}: {{ (trigger.event.data.amount_pence | int | abs) / 100 }} GBP`.
   Save. A no-op sync or duplicate provider item must not create a new outbox row.
   Safe mode supports a generic imported-transaction notification without detail.

### 4. A savings goal reaches 75 percent

1. Opt in to Selected Goals and select the goal(s), or choose All Goals.
2. Create a **Numeric state** trigger on the percent sensor(s), Above `74.99`.
3. Add a celebratory notification action and save. A later crossing of 75 percent
   fires once; staying above it does not repeat each refresh. The opaque entity
   identity survives a goal rename. Choose a fresh crossing to validate the trace.

### 5. A subscription's observed price rises

1. Enable Detailed event data with the app administrator's consent.
2. Create an **Event** trigger for `family_finance_subscriptions_event`, with event
   data `event_type: subscription.price_observed`; add the intended
   `config_entry_id` and optionally the opaque `entity_id` for one subscription.
3. Add a visual **Template** condition:
   `{{ trigger.event.data.amount_pence | default(0) | int | abs > trigger.event.data.old_pence | default(0) | int | abs }}`.
4. Add a notification message using the supplied payee/date/old/new pence fields.
   Save. A recorded observation with unchanged price fails the condition. Test with
   a new synthetic observation; replaying the same deduplicated observation does
   not produce another canonical event.

For multi-target recipes, select multiple numeric entities in the trigger's entity
picker. Raw Event triggers can use visual Template conditions for an allowed list
of stable `entity_id` values. On an EventEntity state trigger, event details are in
`trigger.to_state.attributes`; raw bus Event triggers use `trigger.event.data`.
These are different HA trigger contexts and should not be interchanged.

### Bill or subscription lifecycle: All, one or several

Create an **Other triggers > Event** trigger for `family_finance_bills_event`
(or `family_finance_subscriptions_event`). In the visual event-data form, set
`event_type` to a supported type, such as `bill.paused` or
`subscription.cancelled`, and `config_entry_id` to the intended connection.
For All authorised items, leave `entity_id` unset. For one item, enter its opaque
`entity_id`. For several, leave that field unset and add a visual **Template**
condition such as `{{ trigger.event.data.entity_id | int in [17, 23] }}`, using
IDs from synthetic event traces. These are form fields, not YAML editing.
Use generic notification wording in Safe mode; Detailed mode can expose the
name/payee and amount. Exercise a real mutation, then an unchanged replay, and
inspect the trace. Installed HA visual-editor and notification delivery acceptance
remains tracked in #441; synthetic harness tests do not claim that acceptance.

## Configured energy-statistics ingestion

Set the meter entity in the app's Energy configuration. With HA recorder enabled,
the integration requests daily recorder `change` statistics in **Wh**, ending at
the current HA local midnight, for a bounded seven-completed-day rolling window.
Recorder handles total-increasing meter resets and unit conversion. The integration
never subtracts raw cumulative states or invents consumption from a missing sample.

Only rows spanning a complete HA local day, including 23/25-hour DST days, with a
finite nonnegative change are eligible. Missing, negative, non-finite or partial
rows are skipped. Known zero consumption is a valid reading. Changed daily values
are posted to `/api/v1/energy/readings` with source `ha_statistics`; the app upserts
by household/date, so reload retries cannot duplicate a day. Unchanged values are
suppressed within a coordinator session. Corrected recorder history is picked up
within the rolling window. Supplier bills remain actual ledger transactions;
energy costs from these readings and tariffs remain estimates.

The HA APIs used here are documented by the
[Event entity developer reference](https://contributors.home-assistant.io/core/entities/event/)
and [Recorder statistics reference](https://www.home-assistant.io/actions/recorder.get_statistics/).
Local tests exercise synthetic recorder responses and the actual app adapter
against disposable FastAPI databases. They do not establish real meter completeness,
HAOS hardware compatibility, notification delivery, or editor screenshots.

## Canonical family transitions and restart-safe comparison state

The backend keeps one compact prior-state snapshot per budget, seasonal event,
energy period and payslip in `app_meta`. A financial mutation and its snapshot /
outbox updates share one database transaction: rollback removes both. The first
observation of existing data seeds a baseline; it does not invent old transitions.
Normal scheduler wakes evaluate households even when no notification rule exists.
The scheduler compares authoritative entity values, not rising summary counts.

Budget usage uses the same signed, split-aware, as-of expense flow as Budget's
headline, including unplanned and uncategorised expenses and net refunds. Shared
canonical events include shared account flow; owner budgets include that owner's
flow. An unchanged poll, void row, future-dated row or unrelated owner's private
spend cannot manufacture a usage transition. `budget.usage` carries actual plan,
spent, remaining and percent values in detailed mode. `budget.over` fires on a real
crossing below zero remaining; `budget.remaining_changed` requires the remaining
amount itself to change. Detailed events include `old_remaining_pence` and
`old_usage_pct`, so an automation condition can compare old and new values for
its chosen threshold without firing repeatedly while already below it. Feed
timestamps carry an explicit UTC offset; legacy naive outbox timestamps are
interpreted as UTC. Ordinary transaction/split/status/correction/bulk writes,
bank page checkpoints, settlement classification and budget revisions evaluate
inside their actual mutation boundary.

`seasonal.variance` compares recorded gift actuals and commitments separately;
remaining is budget minus actual, while remaining after commitments retains its
planning meaning. `energy.variance` compares the authoritative metered estimate
against approved/locked shared energy plans. Missing readings are explicit in its
status and payload, and missing tariffs are unknown rather than zero. Known zero
consumption days count in the projection average; missing days do not. Shared
energy report responses apply the same private-plan exclusion.

[Loan balance and fully-repaid events](HA-LOAN-EVENTS.md) extend the canonical
family streams without inferring repayment due dates from monthly amounts.

[Explicit occasion dates](SEASONAL-DATES.md) enable `seasonal.approaching` reminders
without guessing dates for existing seasonal records.

`payroll.approaching` is a durable time event in the three-day lead window of a
real employment/payroll expected occurrence. It deduplicates on employment plus
due date, including refresh, reload and catch-up inside the window. Employment
ownership controls visibility even with a shared or absent destination account;
the shared HA service does not receive private employment events. Regenerating
an expected occurrence does not discard the event's ownership reference.
`payslip.state_changed` compares
persisted lifecycle status only and never includes a filename, file path, document
text or extraction contents. Private occurrence/artifact events remain private in
the feed even in Safe mode.

Successful syncs retain `sync.completed`; a failed-to-completed transition also
emits `sync.recovered` once. Raw provider exception text is never included in the
outbox, including Detailed mode. Manual and scheduled backup failures emit
`backup.failed` once per failure streak, with constant status/source/date only;
a successful verified backup clears the streak. A failed manual request persists
that sanitised failure separately from request rollback. No snapshot filename or
exception text is included in either detail mode.
