# Changelog — Family Finance add-on

## Unreleased

## 1.5.7

- Guide Shared contributions from the budget, using each member's pay cycle and
  showing the next payment separately (#627, #628, #656).
- Ask for approval before changing agreed contributions, with automatic or
  delegated approval available in household settings (#657).
- Count eligible direct payments once and estimate bills-account top-ups from
  upcoming bills, available funds and a chosen buffer (#658, #659).
- Filter Bills and Subscriptions by payment account within All, Mine or Shared
  (#661).

## 1.5.6

- Exclude archived accounts from actionable bills, forecasts and reconciliation.
  Use remaining unpaid amounts, preserve write-offs and protect scheduled
  payments when accounts merge (#587, #589, #590, #610, #611, #622).
- Keep loan balances, repayment evidence, interest, payoff dates and monthly
  net worth consistent after settlement or reversal (#591, #605, #607, #616, #617, #626).
- Count confirmed transfers in reports, display savings rates as percentages and
  show above-target income as favourable (#592, #597, #606).
- Validate backups before restoring, handle valid SQLite paths and avoid sending
  the same pending Home Assistant notification twice (#594, #595, #615, #623).
- Distinguish unavailable data from zero, make repeat energy entry work, and
  label energy input as daily consumption (#602, #603, #619).
- Restore keyboard focus after closing mobile dialogs (#596).

## 1.5.5

- Preserve incomplete historical payroll and dependent forecasts when overtime
  offsets the gross difference. Flag the forecast for review.

## 1.5.4

- Open Overdue on Bills to see each outstanding occurrence, its original account, due date and remaining amount. Keep historical occurrences visible when the bill changes dates or payment accounts (#578).
- Resolve archived category names in Budget and Shared, and show planned totals alongside actuals (#577).
- Remove obsolete lines from future editable budgets in their own scope without disturbing historical plans or personal bill schedules. Skip archived categories when copying plans (#579, #580).
- Use the same default account on Dashboard and Forecast. Align the calendar with the selected account and retain an all-accounts view (#572, #573).

## 1.5.3

- Rebuild incomplete payroll forecasts from the affected payday and refresh dependent periods. Recover eligible missing salary lines when saving bonuses (#564, #566).
- Use the Family Finance logo and icon in the Home Assistant custom integration (#565).

## 1.5.2

- Protect authored splits during automatic categorisation and transfer pairing,
  while retaining legitimate refund and expected-payment matching (#556).
- Recover missing verified payslip inputs through an audited owner-only path,
  preserving historical actuals and refreshing eligible forecasts (#552).
- Show overtime hours and rates with exact per-entry pay and payroll cutoff
  handling; refresh older forecast breakdowns automatically (#553).
- Diagnose legacy bank-mapping constraint drift and provide guarded repair with
  verified backup, collision checks and data-preservation safeguards (#550).

## 1.5.1

- Harden payroll deductions and automatic forecast refreshes while preserving
  verified payslip actuals. Combine overtime lines at the same hourly rate.
- Support employer bonus and salary shareholding workflows with compact entry
  controls and combined pay-month deductions.
- Default Overview to the signed-in member's salary payment account, prompting
  when no unique account is available. Keep both projections aligned.
- Enforce banking account scope, owner-only payroll edits and explicit payroll
  read grants for Home Assistant. Preserve historical notes in an audited archive.
- Restore canonical financial automation transitions and explicit seasonal dates.
- Restore startup after combining seasonal-date and payroll/bonus updates by
  joining their migration branches without changing existing revision history.
- Back up before upgrading. Migration 0075 joins the seasonal and payroll schema
  branches; rehearse upgrades and restoration on a copy. Live HA, bank-provider,
  mobile and private-source acceptance remain separate tracked work.

## 1.5.0

- Personal and historical Budget plans follow adjusted payday-to-payday windows;
  Shared and All keep calendar months. Received income and verified contributions
  remain separate from opening funds and manual income-plan lines.
- Overtime, including historical entries, is grouped into Monday–Sunday weekly
  sheets with weekly submit/approve/reject and individual actions.
- Preserve confirmed internal-transfer portions consistently across Budget,
  reports, analytics and forecasts, including deleted transfer-rule history.
- Forecast only the unpaid part of expected income and bills. Validate payment
  corrections and refunds without counting void history or duplicate aliases.
- Make bank refresh ownership, queued requests, retry deadlines and completed-feed
  balance-repair guards consistent across manual and scheduled refreshes.
- Recover malformed Dashboard layouts and show controls to retry or revert a
  failed preference save.
- Reconcile received Budget income by category and plan coverage, with links to
  exact transactions and labelled Shared/member sections in All, including
  unplanned and uncategorised activity.
- Update frontend dependencies and image-build actions.
- Back up before upgrading: startup applies migration
  `0070_transfer_confirmation_history`. Verify backup restoration and rehearse
  migration on a copy before deploying.
- Published v1.5.0 contains the finance fixes and dependency updates listed above,
  promoted from `develop` to `main` through release PR #499.
- Actual HA/provider/mobile acceptance, private categorisation configuration and
  sixteen recovered workbook-comment mappings remain tracked work. This update
  does not claim those operational checks or private data changes are complete.

## 1.4.2

- Fix dashboard outage recovery, Budget MTD totals and default All navigation.
- Repair scoped categorisation, transfer matching and readable category choices
  while preserving reconciled history.
- Fix payroll/tax, goal, loan and seasonal accounting and privacy regressions.
- Restore private monitoring permissions and exact transaction search links.
- Acknowledge HA outbox cursors after event publication; improve HA events,
  optional entities and energy statistics.
- Partial payments emit `movement.partial`; only completion emits
  `movement.settled`, without duplicate events on replay.
- Back up before upgrading: startup applies migrations 0068 and 0069. Private
  space-transfer aliases require configuration after deployment; no live rules
  were changed during verification.
- Workbook-comment mapping and physical HA/operator acceptance remain open;
  see the full repository changelog and regression review for limitations.

## 1.4.1

- Financial widgets show request failures explicitly and preserve old figures
  only while refreshing the same account or budget.
- Fix member-scoped forecasts and personal Budget actuals, bank writer windows,
  historical refresh handling and partial-payment settlement.
- Repair effective payday forecasts and preserve settled history when the salary
  destination changes.
- Record only matched subscription payment amounts and finalize failed syncs
  without retaining incomplete pages.
- Historical audit follow-ups remain open, including Budget MTD accounting,
  ordinary payroll generation and additional financial/access-control and Home
  Assistant acceptance. Review the release PR before promotion to main.

## 1.4.0

- Bank categories now feed your categories automatically — map each
  provider label once on the Rules page and every synced transaction
  files itself (issue #453).
- Salary appears in forecasts: set each employment's paid-into account
  and projected paydays carry estimated income until a real payroll
  run takes the date over (issue #454).
- Transactions that reconcile to a bill or subscription inherit its
  category, and every match teaches the categoriser the payee —
  manual categorising shrinks toward zero (issue #452).
- Fixed intermittent 503s on dashboards and widgets while a sync or
  backup ran — background work no longer blocks the interface
  (issue #451).
- Payroll runs created before rule-set tracking now record which
  year's rules computed them; dev-stack notification delivery works
  end-to-end.

## 1.3.0

- Settings gains an in-app Starling sync interval (15 min – 24 h) —
  no add-on restart needed; the add-on/env override still wins.
- Incoming bank payments now reconcile automatically after sync:
  matching bills mark paid and budgets/expected movements settle.
- Dashboard widgets recover from a failed data fetch instead of
  staying empty until reload.
- Pages can no longer scroll sideways on narrow screens — a
  system-wide overflow fix covering all routes at 320px.
- License changed to Personal Use (free for personal use; no
  commercial use, AI training or content republication).
- The retired Excel workbook import machinery is removed; CSV/OFX/
  QIF statement import is unaffected.

## 1.2.0

- Bank balances, account renames and closures now sync automatically
  on the scheduled cadence — previously only transactions updated
  until a manual sync.
- Opt-in Starling webhook option (`starling_webhook_public_key`) for
  near-real-time refresh — polling remains the default.
- Bills page: overdue items get their own tile, Next due only shows
  upcoming dates, and summary tiles no longer overflow on mobile.
- Dashboard widgets no longer vanish after navigating away.
- Budget page gains a "Bills remaining" tile and clearer
  planned/spent/remaining semantics.

## 1.1.2

- 2026-27 UK payroll rule sets — current-tax-year payroll uses exact
  published parameters.
- Ingress identity hardening: no `X-Profile-Id` or localStorage
  identity trusted under ingress.
- Observability: operations runbook (`docs/OPERATIONS.md`), release
  gate + perf tooling, ingress trust audit matrix.

## 1.1.1

- Add-on card keeps the name **Family Finance**; the HACS integration
  is now **Family Finance (Entities)** so the two update cards are
  distinguishable.
- This changelog file ships with the add-on — update cards now show
  release notes instead of "No changelog found".

## 1.1.0

- Add-on icon and logo.
- Backup list: newest first, Latest badge, readable labels, and the
  snapshot button locks while a backup runs.
- Admin action to link a Home Assistant identity to an existing
  profile.
- Bank-ledger integrity fixes; performance budgets; blocking lint and
  type checks; migration parity tooling and workbook-retirement
  sign-off.

## 1.0.5

- Ingress `basename` derived at startup — fixes the blank white panel.
- Healthcheck `start-period=120s` — fixes the first-boot restart loop.

## 1.0.3

- Empty Supervisor options (`""`) no longer crash second boot onward.
- Empty `X-Admin-Token` no longer satisfies the comparison.
- CRLF hardening for the entrypoint on Windows checkouts.

## 1.0.2

- Ingress auth + fresh-install bootstrap fixes.

## 1.0.1

- Add-on options `admin_token`/`backup_dir` default to empty strings
  the Supervisor accepts.
- Entrypoint chowns `/data` before dropping privileges — fixes the
  root-owned `options.json` start crash.
