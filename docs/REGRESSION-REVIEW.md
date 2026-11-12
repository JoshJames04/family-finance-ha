# Historical regression review

This change follows the v1.4.1 reports and the historical issue audit.
Tests use synthetic data. Production data, bank credentials and snapshots
are excluded from the repository.

## Recovery and data provenance

Transient API failures show an error after the initial retry burst, then
retry every 30 seconds. Returning online or returning to a visible page
also retries failed requests. A failed identity request remains a failure;
it never becomes a successful empty profile list. Cached data belongs to
its household, profile and resource parameters. Refreshing that same
resource can retain its last successful response; changing the resource
clears it immediately.

If an upgrade fails, the pre-upgrade snapshot is restored, readiness stays
degraded, financial API mutations return 503, and the scheduler remains
disabled. Administrator recovery endpoints remain available. Readiness
exports a fixed failure code, without database exception details.

Ingress also preserves token-authenticated administrator backup/restore access
through the existing admin gate. The restricted HA service endpoint allowlist
does not strand recovery on a failed upgrade. This does not assign the service
a household member identity.

New snapshots embed their UTC creation timestamp in
`ff_backup_manifest`. Listing reads this timestamp independently of file
modification time or a changed filename; legacy or malformed manifests
fall back to the timestamp in a valid snapshot filename. Snapshot
integrity and restore verification continue to apply.

The system theme follows the accessible Home Assistant frontend's explicit
dark/light setting, falling back to the operating system when the parent
cannot be read. A saved application theme overrides either source.

PR CI regenerates the TypeScript API schema and rejects drift from the
backend, alongside the endpoint contract tests.

## Financial navigation and Home Assistant

The command menu searches authorised transactions by literal payee, notes,
category (including split categories), and a signed or unsigned GBP amount.
It carries the search into the transaction page. Forecast links carry the
source record identity and focus the corresponding bill, employer, pay run,
goal, subscription or transfer rule after its data loads.

Home Assistant per-item discovery is capped at 100 items and applies account
visibility before exposing names or numeric values. Bills use a stable source
identity and the earliest outstanding occurrence; fully settled occurrences
are skipped. Account balances and goal progress use the canonical ledger
functions. Detailed event payloads require an administrator to acknowledge
the event-bus, recorder, trace and notification privacy implications in Settings.
Budget approval/locking, changed energy readings and successful manual or
scheduled backups emit canonical outbox transitions. Reposting an unchanged
energy reading emits no duplicate transition.

## Verification boundaries

The historical audit groups are covered by these regression suites:

| Audit group | Fix / evidence |
|---|---|
| B1, B6, B7 | Complete signed, as-of Budget spending with an itemised bridge; `test_historical_budget_acceptance.py` |
| B2, B3 | Authorised reports and owner-scoped budget variance; same suite |
| B4 | Opening balance stock excluded from earned income; same suite |
| B5 | Partially paid bills retain their remaining obligation; same suite |
| B8 / #456 | Tariff and template deletion lifecycle; `test_budget_register_acceptance.py` |
| P1, P2, P12 | Employment, payslip, goal and payee privacy; `test_historical_payroll.py` |
| P3–P5 | K-code limits, cumulative refunds, NI categories and versioned flat tax rates; historical payroll and `test_paye_refunds.py` |
| P6, P7 | Unique loan payment attribution and sequential repayment balances; historical payroll suite |
| P8, P9 | Dated, non-void goal funding; historical payroll suite |
| P10, P11 | Ordinary payroll generation identity and operational cutoff; historical payroll suite |
| P13 | Seasonal visibility, actual versus committed costs, visible/searchable notes; original workbook annotation mapping remains unverified |
| F1 | Real-route ingress, viewer and shared-service authorization; `test_banking_security_regressions.py`, `test_rule_privacy.py` |
| F2 | Real HA adapter against synthetic FastAPI; `test_ha_adapter_contract.py` |
| F3, F5 | Scoped outbox, safe monetary redaction, class-only exception exports; banking security suite |
| F4 | Quiet first connection/reload and durable cursor delivery; `tests/ha/test_outbox.py`, `test_event_entities.py` |
| F6 | Cursor/EventEntity/options, canonical families, energy recorder ingestion, HA theme, financial search, exact source links, schema CI, snapshot manifests and migration write guard; dedicated HA, recovery, search and canonical-event suites |
| F7 | Shared and owner-aware overspend evaluation; banking security suite |
| Review followups | Scoped learned policies, immutable historic rows, native event batch delivery, renamed sensor names and correct All period selection; dedicated rule, immutability, HA and browser regressions |

Synthetic browser evidence contains no household data:

![Combined Budget](images/historical-budget.png)

![Alphabetical transaction categories](images/historical-categories.png)

Passing synthetic tests, a successful container startup and a published
release are separate facts. Actual HAOS device runtime, the visual Home
Assistant automation editor, real bank-provider sync and real notification
delivery require operator checks; this review does not fabricate those
results. This PR is for review and does not deploy or change live data.

The retained workbook's 16 threaded comments are recoverable. Exact assignment
to application records is unverified; see the finance disposition. These limits
remain explicit review items, rather than inferred acceptance from closed issues.
