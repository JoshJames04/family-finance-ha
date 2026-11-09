# Home Assistant notification triggers

**Status:** Plan — trigger side only (issue-tracked implementation to
follow). Covers how Home Assistant detects that Family Finance needs
attention and turns that into a notification the user actually sees.

## 1. What already exists

**Backend — a real notification engine** (`app/domain/notifications.py`):

- 12 rule kinds: `spend_above`, `balance_below`, `weekly_digest`,
  `payday_reminder`, `bill_reminder`, `low_projected_balance`,
  `budget_overspend`, `unusual_transaction`, `price_change`,
  `missed_transfer`, `sync_failure`, `payroll_variance`.
- Rules evaluate into `NotificationEvent` rows; a dispatch pass
  delivers each pending event once; events support `ack`/`snooze`.
- `payroll_variance` already fires when a confirmed payslip disagrees
  with the forecast run — the reconciliation prompt the user asked for
  is already emitted backend-side.
- Payslips carry a `PayslipStatus` lifecycle
  (`uploaded → extracted → needs_review → confirmed | failed`), so
  "waiting on the user's review" is countable server-side.

**HA integration — a transition-event bus** (`custom_components/
family_finance/events.py`, issue #102):

- The coordinator polls `async_fetch_summary()` every interval and
  `process_summary()` diffs consecutive payloads.
- Rising counts fire sanitised HA-bus events —
  `family_finance_bill_due`, `family_finance_sync_failed`,
  `family_finance_goal_milestone`, `family_finance_low_balance`.
- Privacy contract already established: payloads carry counts, flags
  and enum fields only — never payees, notes or amounts.

## 2. The trigger mechanism (what to build)

HA does not need a webhook — the existing poll-and-diff pattern covers
every notification, including the payslip review prompt.

### Backend: expose two countable states

| Counter | Source | Fires when |
|---|---|---|
| `payslips_pending_review` | `GET /api/v1/payslips` filtered to `status=needs_review` | a payslip has been uploaded/extracted and awaits manual review |
| `notifications_pending` | `GET /api/v1/notifications/events` filtered to unacknowledged | any rule (variance, sync failure, unusual spend, …) has delivered an event the user has not acked |

If the events list cannot filter server-side today, add a
`?acknowledged=false` query param — cheaper than shipping the whole
history to the integration.

### Integration: two new summary fields, two new events

`async_fetch_summary()` gains:

```python
payslips = await self._get_optional("/api/v1/payslips")
pending_reviews = sum(
    1 for p in (payslips or {}).get("items", [])
    if p.get("status") == "needs_review")
notif_events = await self._get_optional(
    "/api/v1/notifications/events?acknowledged=false")
```

`events.py` gains, following the exact `process_summary` pattern:

- `family_finance_payslip_review` — fires when
  `payslips_pending_review` rises (payload: `pending_count`).
- `family_finance_notification_pending` — fires when
  `notifications_pending` rises (payload: `pending_count`, and if the
  API can group them, the distinct `kind` values present — enum names
  only, still no figures).

Rise-not-presence keeps notifications actionable: the event means
"something new needs you", not "the queue is non-empty".

### HA side: user automations on the bus events

The integration stays delivery-agnostic; the user wires the event to
whichever notifier they run (mobile app, persistent notification,
TTS). Shipped example automation for the docs:

```yaml
alias: Finance — payslip awaiting review
triggers:
  - trigger: event
    event_type: family_finance_payslip_review
actions:
  - action: notify.notify
    data:
      title: "Family Finance"
      message: "A payslip is extracted and awaiting your review."
      data:
        url: /family-finance        # ingress panel
        clickAction: /family-finance
```

And the generic catch-all:

```yaml
alias: Finance — notifications pending
triggers:
  - trigger: event
    event_type: family_finance_notification_pending
actions:
  - action: persistent_notification.create
    data:
      title: "Family Finance"
      message: "{{ trigger.event.data.pending_count }} notification(s)
               need attention in the Finance panel."
```

### Entity triggers (alternative to events)

The same two counters are also exposed as entities, so a state
trigger works too — useful when the automation editor's state picker
is easier than writing an event type:

| Entity | State |
|---|---|
| `sensor.family_finance_notifications_pending` | count of unacknowledged app notifications |
| `sensor.family_finance_payslips_pending_review` | count of payslips awaiting review |

```yaml
alias: Finance — payslip awaiting review (entity)
triggers:
  - trigger: numeric_state
    entity_id: sensor.family_finance_payslips_pending_review
    above: 0
actions:
  - action: notify.notify
    data:
      title: "Family Finance"
      message: "A payslip is extracted and awaiting your review."
```

Both fire on the same underlying count — events for one-shot
notification, entities for automations that prefer state conditions
(e.g. "and still pending after 1 hour" reminders).

## 3. The payslip flow end-to-end

1. User uploads a payslip in the panel → status `needs_review`.
2. Extraction runs locally → review dialog pre-filled (advisory only).
3. Next coordinator poll sees `payslips_pending_review` rise →
   `family_finance_payslip_review` fires → HA notifies.
4. User reviews and confirms → payroll run reconciles.
5. If actuals differ from forecast → `payroll_variance` event →
   `notifications_pending` rises → second notification ("the OCR
   pushed it for review, now tell me what changed").
6. User acks the event in the panel → count falls → no re-firing.

The user does the final reconciliation decision every time; OCR and
notifications only ever push work *to* them.

## 4. Deliberate constraints

- **Poll, don't push.** No new ports, no HA→app webhook — the
  coordinator already authenticates and polls. Latency is one poll
  interval, acceptable for finance alerts.
- **Sanitised payloads forever.** Event data is counts/enums only;
  amounts, payees and salary figures never enter the HA state machine
  (they would persist in `home-assistant_v2.db`).
- **Debounce by construction.** Rise-detection + one-`sync_failed`-per
  -streak already prevent spam; repeated failed polls and flapping
  counts do not re-fire.
- **Ack is the off-switch.** Counts fall only when the user acts —
  no auto-clearing notifications.

## 5. Out of scope for the trigger pass

- Choosing notification channels (mobile push vs persistent vs TTS) —
  user-side automation config; examples above ship in docs.
- Rich/digest notifications (weekly summary content) — `weekly_digest`
  rule exists backend-side; the pending counter surfaces it like any
  other event.
- Snooze/ack from the HA side — nice-to-have service call later
  (`family_finance.acknowledge_notification`); not needed to make the
  triggers useful.
