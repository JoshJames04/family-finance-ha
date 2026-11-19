# Automatic payroll and compact review plan

**Goal:** Generate payroll from employer settings and refresh eligible forecasts on changes, without routine Add run, Reconcile or Review forecasts steps.

**Spec:** User requests of 18 November 2026: automatic event-driven calculations; a compact 50/50 Breakdown with an all-employer monthly summary and year filter; bonus work in a separate PR.

**Design:** Reuse the guarded payroll rebuild and existing calculation engine. A shared automation service creates missing current/next-year periods and refreshes stale unconfirmed forecasts in the source transaction. Mutation routes, startup/daily maintenance and an owner-scoped page refresh endpoint call the same service. Protected actuals, bank matches, overrides and pending corrections remain unchanged. Incomplete inputs produce an actionable issue, never invented actuals. The regular screen shows forecasts without requiring payslip reconciliation; private payslip checking/corrections remain optional.

**UI:** Keep existing components/tokens. Breakdown uses two equal desktop columns, stacking on smaller screens. Left: compact monthly gross/net/status table, all employers, year selector. Right: employer sections with year-filtered current rows and collapsed details; superseded corrections are optional history. No Add run action. Bonus controls belong only to PR #540's employer configuration UI.

**PR boundaries:** #543 contains this engine/automation/layout work. #540 follows it with a separate bonus diff; retain all pending shareholding/YTD safeguards. Local preview combines both. No merging, release, production writes or private financial fixtures in Git.

## Tasks

- [x] Add failing domain/API tests for automatic creation, overtime approval/edit/removal, salary/tax/calendar changes and actual-driven dependent refresh. Cover protected history, incomplete YTD, idempotency and missing setup.
- [x] Add `domain/payroll_automation.py`; reuse `generate` and preview/fingerprinted apply. Route source mutations and startup/daily maintenance through it; expose owner-scoped refresh outcomes and errors.
- [x] Add failing UI tests for automatic refresh, the year filter, combined monthly totals, compact employer/history disclosure and absence of manual run actions.
- [x] Refactor Salary Breakdown to the requested 50/50 layout; keep optional private payslip checking and explicit actuals correction paths.
- [ ] Update reliability docs, generated API types and the current PR. Run the full backend/frontend suites, static checks, builds and native review; fix verified findings.
- [ ] Integrate both PRs into the local preview, verify historical actuals/bank data are unchanged, and prepare at most five local acceptance checks.

## Review focus

- Refresh must finish after source changes are flushed, before reporting success.
- A failed estimate must not roll back a valid overtime/configuration change or overwrite existing verified amounts.
- Automatic generation must preserve correction chains and avoid duplicate periods/income.
- Opening a historical year must not fabricate historical actuals.
- Bonus changes and their migrations stay out of the engine PR.
