# Changelog — Family Finance add-on

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
