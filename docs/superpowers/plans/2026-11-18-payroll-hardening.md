# Payroll hardening implementation plan

**Goal:** Make statutory forecasts reproducible, and prevent incomplete payroll records from becoming trusted actuals.

**Architecture:** Keep the shared integer-pence calculator; implement HMRC rounding and published period thresholds there. Store calculation inputs separately from immutable confirmed payslip facts. Resolve correction chains once and use one totals implementation across API and domain. Never infer missing historical facts from current employer settings.

**Spec:** User-approved payroll reliability analysis and implementation request, 18 November 2026.

**Constraints:** One PR to develop. Local preview before approval. No production writes. Only synthetic test fixtures in Git. Preserve historical actuals, overrides and bank transactions. Bonus development remains separate.

## Tasks

1. Add failing independent HMRC vectors for free pay, whole-pound taxable pay, ordinary-code withholding limits, published monthly/weekly NI thresholds, NI penny rounding and whole-pound loans on NI earnings. Correct shared formulas; retain versioned parameter history. Check negative inputs and unsupported categories/codes explicitly.
2. Add failing salary/calendar tests. Provide an explicit equal-monthly salary policy alongside existing daily proration; keep overtime cutoff independent of the base salary month. Distinguish cash and statutory dates for regular early payments; select tax code on the statutory payday.
3. Add failing historical/YTD/correction tests. Store verified taxable/NI/YTD facts and calculation snapshots. Reject ambiguous prior actuals rather than reconstructing with today's pension settings. Follow latest correction, canonicalise fiscal membership and preserve prior actuals.
4. Add failing confirmation/refund/duplicate-line tests. Require explicit actuals and independently entered payslip gross/net; reject ambiguous kind-level allocation; share signed totals with API. Provide clear UI validation. Mark dependent forecasts stale and offer a guarded preview/rebuild preserving overrides.
5. Investigate overtime using private source evidence, test snapshot rates/cutoffs and avoid inventing missing work entries. Run full backend/frontend suites, migration up/down/up, lint and type checks. Replay private known inputs without publishing them. Deploy combined local preview, verify five user acceptance flows and create a non-draft bot PR with closing tags and Josh requested for review.

## Review focus

- Refunds and pension refunds must retain their signs and source tax bases.
- Early payday across 6 April must not silently select the wrong fiscal year.
- Incomplete imports, duplicate component kinds and missing YTD must fail safely.
- Corrections and later forecasts must not double count or overwrite confirmed history.
- Salary changes and overtime cutoff windows must not change fixed monthly base pay.

## Progress

- Started from develop a9d6851 in isolated branch fix/payroll-formula-hardening.
- Existing pending bonus work and its uncommitted YTD guard left untouched.

- Shared formulas, explicit actuals/YTD/date evidence, correction chains, guarded rebuild and dependency invalidation implemented.
- Independent review findings reproduced and covered: cumulative BR/NT, band-boundary selection, Plan 5 start date, protected piecework, fiscal ordering, missing PAYE/YTD periods, immutable reopen and rebuild collisions.
- Verification: full backend 1,978 passed; final strengthened signature guard covered by 85 targeted tests; frontend 248 passed; lint/mypy/typecheck/build passed. Migration up/down/up and retained database triggers covered in the suite.
- Local combined preview deployed and real forecast preview verified without changing confirmed actuals or bank data. Synthetic preview/apply and incorrect-net rejection verified through the local API. Rendered confirmation/review controls inspected. Bonus feature integration remains a separate review slice.

- Local legacy rule-year collision and unsigned unpaid-leave cases reproduced, fixed and covered. Offline API contract fuzzing rejects generated bank tokens without contacting the provider.

- Native Codex findings reproduced and fixed: final cutoff-window overtime for equal-monthly leavers (including December/January), and gross-only optional taxable pay in both confirmation dialogs. Latest focused backend suite: 164 passed; frontend: 252 passed with typecheck/build. Full reviewed backend verification is running separately.

- Second native review: regular forecast cash dates now follow corrected payday rules; saved original calendar and payroll-month provenance retain one-off dates and leaver cutoffs. Empty piecework rows retain identity but no longer emit zero income. Reproduced calendar cases pass; affected backend suite 174 passed; frontend 252 passed with typecheck/build. Prior full reviewed backend suite passed 1,983 tests; a final full run will include these calendar cases.
