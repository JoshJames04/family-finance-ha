# Work remaining after release 1.4.2

Tracking snapshot: 12 November 2026. This is the expected position after release
PR #474 merges into `main`; it is not a claim that publication or live acceptance
has already happened. Version preparation #473 must reach `develop` first.

## Issues completed by the release

#474 has a separate closing keyword for each of #456, #466, #467, #468, #438,
#439, #440 and #476. These cover register deletion, All Budget navigation, category
ordering/contrast, the HA outbox consumer, optional entities, event families and
correct partial-versus-final settlement events.
They remain open until the release reaches the default branch.

## Eight remaining tracked issues

| Issue | Remaining result needed | Planned board status |
|---|---|---|
| [#360](https://github.com/JoshJames04/family-finance-ha/issues/360) | Map all 16 recovered source comments into appropriate app records with provenance. Current notes support does not preserve those originals. Keep the private recovery inventory outside GitHub. | Ready |
| [#414](https://github.com/JoshJames04/family-finance-ha/issues/414) | Verify saved dashboard order/visibility across routes, refresh, login and actual HA ingress. Coordinate with #460; earlier user confirmation of layout improvement did not finish data acceptance. | In Review |
| [#460](https://github.com/JoshJames04/family-finance-ha/issues/460) | Validate populated dashboard navigation and recovery on the released add-on under actual ingress/sync/backup load. Browser-injected outage tests passed; the required live/user confirmation is still pending. | In Review |
| [#461](https://github.com/JoshJames04/family-finance-ha/issues/461) | Verify both real live/local employment configuration, generated movements, account projection and actual-versus-expected Budget income after upgrade. Code and synthetic checks do not complete this issue's mandatory live evidence. | In Review |
| [#416](https://github.com/JoshJames04/family-finance-ha/issues/416) | Observe a genuine unattended transaction-and-balance sync after the configured interval, including restart/backoff/idempotency and identifiable post-sync reconciliation. Do not substitute a manual sync or scheduler unit test. | In Review |
| [#432](https://github.com/JoshJames04/family-finance-ha/issues/432) | Finish and verify broader All/selected targeting and threshold UX across the requested financial families. Current bill/account/goal numeric entities and family event streams do not certify every requested editor flow. | In Progress |
| [#441](https://github.com/JoshJames04/family-finance-ha/issues/441) | Capture real HA visual-editor examples/screenshots and verify actual trigger variables, multi-target filtering, ingress and live delivery. Five written recipes already exist. Physical supported-install acceptance remains unverified. | Ready |
| [#475](https://github.com/JoshJames04/family-finance-ha/issues/475) | After deployment and a verified live backup, apply the privately confirmed scoped categorisation policies and verify new syncs without altering protected historical records. Configuration was saved locally only. | Blocked: awaiting release/deployment |

The four dashboard/payday/sync issues above remain open deliberately: their
descriptions explicitly require actual installation evidence. A closing keyword
must not turn missing acceptance into a completed task.

## Project board access

The personal **Family Finance** Project V2 board is the repository's intended
status source. Its publishing GitHub App returns **Resource not accessible by
integration** when reading it. Josh requested bot-only access, so no personal
credential fallback was used. Issue records and PR references were updated;
the table above records intended board changes, not successful board writes.

Once supported bot access exists, reconcile all board items against current issue
states, move release-completed items to Done after promotion, apply the remaining
statuses above, and keep parent #432 active while #441 or broader acceptance is
unfinished. Do not mark an epic Done solely because its implementation children
closed.
