# Loan automation events

The Loans event entity and `family_finance_loans_event` stream publish two
canonical backend events:

| Type | Meaning |
|---|---|
| `loan.balance_changed` | The dated outstanding balance differs from its last observed committed value. |
| `loan.fully_repaid` | That balance changed from positive to zero. |

Both events retain stable `entity_kind=loan` and `entity_id`. Use the native HA
event trigger with event type `family_finance_loans_event` and event-data filter
`event_type: loan.fully_repaid`, optionally adding a particular `entity_id`.
Omitting the ID selects all loans visible to the integration identity; it never
grants access to another member's private loans. This extends the family-stream
approach in [ADR-0009](adr/0009-ha-automation-v2.md).

Safe payloads contain `source`, `status`, `date`, `direction` and `currency`.
Existing explicit administrator opt-in to detailed HA event data adds `name`
(loan party), `old_outstanding_pence` and `outstanding_pence`. Loan/repayment notes
and bank references are never included. Existing financial-detail warnings and
household/member filtering apply in both modes. HA events, recorder, traces and
notification providers may retain opted-in details.

Outstanding uses the Loans-page calculation: principal, configured accrued simple
interest and recorded repayments effective by today. A manually recorded
repayment is not proof of bank settlement. A raw bank import or expected monthly
amount is not treated as a paid loan. The model has no exact repayment due-day
rule, so this slice does not emit due/overdue events or invent a schedule.

Existing loans seed quietly without replaying old balances. Loan/repayment writes
record changes inside their transaction; rollback removes snapshot and events.
Scheduler evaluation also observes dated changes such as accrued interest and
future repayments becoming effective. Repeated unchanged evaluations emit
nothing. Removing a repayment can reopen a balance; a later real return to zero
is a new fully-repaid transition with a distinct deduplication revision. Ownership
changes and reactivation establish a fresh baseline instead of publishing old
private/inactive changes.

For acceptance, create a synthetic loan locally. Record a partial repayment, then
the remainder. Verify one balance change per write and one fully-repaid event at
zero, matching Loans-page values. Remove the final repayment and check the reopened
balance. Refresh/reload without new writes: no extra event. Test Safe mode before
opting into details, and confirm a private loan is absent from a shared-only
integration. Actual HA visual-editor evidence remains tracked by #441; automated
tests do not replace that acceptance.
