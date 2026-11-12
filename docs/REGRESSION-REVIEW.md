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

## Verification boundaries

Passing synthetic tests, a successful container startup and a published
release are separate facts. Actual HAOS device runtime, the visual Home
Assistant automation editor, real bank-provider sync and real notification
delivery require operator checks; this review does not fabricate those
results. This PR is for review and does not deploy or change live data.
