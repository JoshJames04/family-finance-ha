# Home Assistant automations and energy statistics

The integration polls the app every minute. It exposes summary sensors, eight
compatible device triggers, durable per-family event entities, and optional numeric
sensors for individual Bills, Accounts and Goals. The app remains authoritative for
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

## Version 1 payload catalogue

The integration publishes `event` entities for bills, banking, budgets,
subscriptions, goals, payroll, gifts, energy and ops. Their unique IDs contain the
config-entry ID and family, not a display name. Canonical events also appear on the
bus as `family_finance_<family>_event` for visual Event triggers.

Every canonical bus payload contains `schema_version: 1`, `seq`, `event_type`,
`entity_kind`, `entity_id`, `occurred_at`, `config_entry_id`, and `household_id`, plus
the server-filtered `data` fields. There are no client-side guessed financial
transitions. Unknown future types in a recognised family remain consumable.

| Family | Canonical transition types / source |
| --- | --- |
| Bills | `movement.settled`, `movement.partial`, `movement.overdue` from settlement/overdue processing |
| Banking | `transaction.imported` from a committed imported transaction |
| Subscriptions | `subscription.price_observed` from an observed price |
| Ops | `sync.failed`, `sync.recovered`; `backup.created` from successful backup creation |
| Budgets | `budget.approved`, `budget.locked` from lifecycle changes |
| Payroll | `payroll.generated`, `payroll.reconciled`, `payslip.review_required` |
| Gifts | `gift.purchased`, `gift.paid` |
| Energy | `energy.reading_updated` from a changed persisted daily reading |
| Goals | Per-goal numeric percent is available for thresholds; a raw `goal.*` event requires its canonical backend emitter |

Use only types implemented by the installed app version. An available event entity
is a stream endpoint, not evidence that every conceivable transition exists.
Detailed transaction events can include `payee`, `amount_pence`, `account_id` and
`date`; subscription observations can include `payee`, `amount_pence`, `old_pence`
and `date`. Safe mode excludes names and monetary detail server-side.

The eight existing device trigger names remain: bill due, bill overdue, sync
failed, goal milestone, low balance, notification pending, payday and payslip
review. These remain summary transition signals, not item-specific outbox events.
First successful connection/reload seeds the summary baseline without replaying
historical conditions. Each emitted event includes `config_entry_id`, and device
triggers filter it so another configured household cannot activate the device.

## Cursor, retry and offline behaviour

A new install requests the latest visible event cursor and emits no old history.
A saved `event_cursor` in the config entry survives reload. Later polls drain after
that cursor in order, at most 1000 events per refresh. A failed or malformed drain
leaves the cursor unchanged, allowing retry. No-op/overlapping feed rows do not
refire an entity. Reload catches up on committed events after the saved cursor;
summary baseline suppression does not discard these new durable events.

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
