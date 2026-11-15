# Installed-release acceptance

Use the installed Family Finance add-on through the normal Home Assistant
sidebar/ingress route. Confirm its version first. Keep screenshots, household
amounts, transaction details and logs private. Public results need only the issue,
version, device/browser, pass/fail and sanitised symptoms. Do not reset the database
or reimport the workbook. Current remaining work is tracked in
[the release checklist](RELEASE-1.5.0-REMAINING.md).

## Dashboard layout and data: #414 and #460

1. In Customise, save a recognisable non-default layout: reorder a widget and hide
   one. Record the visible list and order privately.
2. Visit Transactions, Bills and Budget, returning to Dashboard after each.
   Repeat once. Saved widgets and order must remain without Reset to default.
3. Refresh, then test a fresh Home Assistant login. Customise must agree with the
   rendered layout. Check separate members if using different member layouts.
4. Compare populated Accounts, recent Transactions, Upcoming and Budget with their
   source pages using the same scope and period. Personal pay cycles and calendar
   Shared/All periods deliberately differ; compare like periods.
5. Leave Dashboard open through a normal scheduled banking interval, then repeat
   navigation. There must be no persistent 503s or false empty financial values.
   If a request fails, record the time and request ID privately. Retry must recover
   when the service becomes healthy; failed reads must show an error.
   Search that ID in Settings → Add-ons → Family Finance → Log and correlate any
   matching failure with its browser status/time. Inspect scheduler health and
   database-lock/error counters for the same window. Record missing log entries
   rather than inventing them. An ingress response may lack an app request ID;
   in that case compare Supervisor/ingress logs and app request counters by time
   to establish whether it reached the backend. Keep logs and ingress URLs private.
   If there are no failures, retain browser Network response statuses/IDs and
   before/after workload diagnostics. Successful requests are not access-logged;
   mark error-log correlation not applicable for a failure-free run.
6. During a scheduled backup, repeat the same ingress navigation and compare
   populated widgets. Alternatively, an administrator can start one normal backup
   from Settings while the other tab navigates. Confirm backup success and no
   503s, frozen widgets or false empty values. This adds actual backup contention
   to normal sync/notification workload; do not run the destructive/stress harness
   against live or repeatedly create backups.
7. On desktop Chrome, first load the saved layout. In Developer Tools, use
   **Network request blocking** to block only `*/api/v1/profiles/me/dashboard*`
   in this browser tab. Change one widget preference. Confirm the save is shown
   as failed/unsaved, rather than saved. Remove the blocking rule before
   navigation so normal layout reads work again, but do not retry the save yet.
   Navigate away/back and refresh: the unsaved draft must remain with a recovery
   action. Choose **Retry saving layout**, then refresh and confirm the saved result.
   Reopen Customise to compare it. Do not clear tab/session storage during this
   test. This locally blocks only layout requests; it does not stop the add-on,
   bank sync, backup, or another user's connection.

HTTP 503/504 injection and malformed persisted preferences belong in the isolated
automated regression environment. The targeted browser-only failed-save check
above supplies installed-release recovery evidence without corrupting stored
preferences. Do not disrupt the production network or database. Normal live
navigation and automated fault-injection coverage are separate evidence.

## Automatic banking: #416

1. Record the configured interval, last successful sync and next run. Observe a
   naturally occurring new bank transaction/balance change; no extra payment is
   needed for the test.
2. Without pressing Sync, wait for the next due run. Confirm the expected new
   transaction and updated bank balance appear once. A timestamp alone is not a
   transaction-and-balance acceptance pass.
3. For an identifiable expected payment, confirm its expected occurrence is
   reconciled and Bills, Budget and forecast agree. Ambiguous movements should
   remain available for Review.
4. Observe another interval: no duplicate transaction or repeated balance
   adjustment. Arrange a convenient restart of the add-on and confirm scheduling
   resumes without duplicate work. Restart recovery is required for a full PASS;
   postponing it means this part remains NOT TESTED and #416 stays open.
5. If the provider reports a rate limit, record the retry deadline and verify
   recovery after it. Do not repeatedly press Sync or deliberately trigger limits.
   Offline/rate-limit injection belongs in isolated tests.
   Record release-version backoff/recovery evidence from a naturally occurring
   or already recorded retry episode, alongside the automated fault-injection
   results. If no such operational episode is available, mark backoff NOT TESTED
   and retain #416 until that remaining evidence is reviewed; a successful normal
   refresh alone does not complete every acceptance criterion.

Keep copied local provider credentials unscheduled. A copied database does not
prove a new unattended provider refresh.

## Income and Budget: #461

1. Confirm each configured employment has its payday, active dates, salary/tax
   setup and Paid into account. Missing setup should identify the missing field.
2. Compare the next salary in Expected/forecast with Dashboard Next payday. Date,
   account and planned net amount should agree. Future salary must not appear as
   already received.
   In Forecast, explicitly select the **Paid into** account and inspect its
   projected balance on the salary date. Verify the estimated salary is included
   in that day's projected cashflow alongside any other movements that day;
   appearing in the separate Expected list alone is not a pass. Check an existing
   payroll run replaces the employment estimate once, rather than adding both.
3. Open a personal Budget containing known historical salary/spending. Check the
   displayed pay-cycle dates, planned salary, received headline and received
   transaction detail. Detail must sum to the headline. Include a known early
   weekend-adjusted payment when available.
4. Confirm each member uses their own configured personal pay cycle. Shared and
   All remain calendar months. Shared includes joint/shared accounts and member
   contributions; All combines personal/shared activity without treating internal
   transfers as new household income.
5. Reopen an older month and the current month. Navigation/refresh must not create
   duplicate salary expectations. A payroll run replacing an estimate must not
   duplicate expected income.

## Physical mobile layout: #477

Test Shared and Budget in the actual Home Assistant phone app, portrait and
landscape, including bottom safe-area padding. Check All and a personal Budget.

- The page must not pan sideways. A wide detail table may scroll inside its own
  bounded container.
- Complete monetary values and long labels remain readable.
- Match payments, Adjust split, Set a share, account/period selectors and Record
  payment remain visible and tappable. Open/cancel dialogs to inspect controls;
  do not record an extra real payment.
- Repeat in mobile Safari or Chrome. Report device, browser and orientation for
  failures. Synthetic viewport coverage cannot replace physical-device evidence.

## Other remaining acceptance

- **#475:** Verify a fresh restorable backup before live policy changes. Apply
  only confirmed account-scoped aliases through an authenticated administrator
  session. Verify a newly synced reciprocal owned transfer pairs, with amounts,
  payees and unrelated locked/reconciled history unchanged. Code releases never
  copy local private rule configuration to live.
- **#432/#441:** Use the supported HA visual editor for All, one item and multiple
  Bills/Accounts/Goals; selection must survive rename. Exercise documented bill
  lead time, GBP balance/transaction thresholds and goal percentage recipes.
  Inspect real trigger variables and delivery using synthetic data. Check no
  duplicate event on refresh/reload and no private names/amounts with detailed
  access disabled. Use only event types documented as implemented in the current
  catalogue. Keep screenshots private or recreate synthetic records before public
  attachment. Follow [the automation recipes](NOTIFICATIONS-HA.md).
- **#360/#351:** Privately approve each recovered note's current budget/year/category
  destination, or explicit preservation as unmatched historical context. Verify
  all sixteen survive with provenance. Restore the backup into a disposable local
  test instance with copied provider credentials disabled, then confirm the notes
  remain viewable there. Never restore over the live database for acceptance: it
  would roll back newer transactions and interrupt banking authentication/sync.
  Do not guess a gift or BudgetLine mapping from a source cell address.

## Recording results

Report by issue, for example: `#414 PASS — v1.5.0, HA ingress, saved layout retained
through three routes, refresh and fresh login; blocked-save draft recovered and
retry persisted it`. For #460 also record the successful backup-under-navigation
and sync-workload checks. Keep failures open with precise
steps and sanitised symptoms. Record NOT TESTED separately from PASS. A combined session can provide evidence for #414
and #460, but each issue's criteria must pass. Close #416 only after a real
unattended provider refresh plus restart, backoff/recovery and idempotency
evidence, and #477 only after physical-device acceptance. A
partial automation slice references #432 without claiming its whole roadmap
complete. Close #351 only when its children and remaining parity acceptance pass.
