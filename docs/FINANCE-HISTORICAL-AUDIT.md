# Specialist finance historical audit disposition

The November 2026 audit used synthetic data. These fixes apply to current
application workflows; they do not certify a private workbook or rewrite
historical payroll calculations.

| Finding | Current disposition |
|---|---|
| P1 | One employment authorization dependency protects every nested employment and payslip route. Members may read and change their own salary details; viewers cannot change them. Admin retains the existing administrative scope. Unconfigured single-user access retains the application's compatibility behavior. |
| P2 | Savings goals inherit the linked account's visibility, including list, creation, progress, contributions and deletion. |
| P3 | K-code withholding is capped at half the period's gross pay, including cumulative/YTD calculations. |
| P4 | Appended version 2 rules correct employee NI categories H/M/Z above UEL. Version 1 rows and payroll run provenance remain intact. New rule lookup seeds missing versions even when an earlier version exists. |
| P5 | BR/D0/D1 rates use the selected version's band indices and band rates; tax-code parsing contains no flat statutory percentages. |
| P6 | Repayment creation and domain linking reject duplicate attribution, void/transfer rows, mismatched direction or amount, another household, and inaccessible private account payments. Database guards prevent duplicate attribution through concurrent/direct writes. |
| P7 | Repayment history accumulates payments in date/id order; same-day entries show their individual balances. |
| P8 | Goal totals and the contribution ledger exclude void transactions. |
| P9 | Goal funding is dated: start date through the as-of date inclusive. Reusing a pot does not silently allocate earlier deposits. Existing opening savings require an explicit contribution allocated to the goal period; there is no inferred opening allocation. |
| P10 | Ordinary generation returns the existing run for the same employment/period. Corrections continue through the explicit correction workflow. Database guards prevent duplicate ordinary periods. |
| P11 | `payday_rule` persists optional `cutoff_day` (1–31); monthly run generation and prepare-year derive the inclusive period from that calendar. The stored run period drives overtime and payslip review. Omit the key to retain calendar-month periods; weekly/nightly rules retain their own period semantics. |
| P12 | Payee counts include visible accounts only; rename affects visible, writable, editable accounts only. |
| P13 | Seasonal events now support shared/private ownership, inherited by their gifts, totals, comparison and rollover. Remaining budget is budget minus recorded actual costs, matching issue 98; a separate remaining-after-commitments figure retains the planning view. Rollover carries actual unspent budget when requested. Notes are displayed and searchable with their gift context. |

## Accounting and migration limits

The SQLite guards preserve existing financial rows. They block new duplicate
loan attribution and ordinary runs, but do not guess which historical duplicate
to delete or amend. Owners must review any previously created duplicates before
using those affected balances. Updating unrelated fields on old rows remains
possible. No existing financial amount is silently changed by migration 0068.

Gift actual costs are explicitly recorded by the user; optional bank transaction
links remain an explicit payment workflow. Automated bank-match discovery is not
claimed. Private payment accounts require the same private event owner, so a
shared event cannot disclose private transaction references.

The retired importer did not retain workbook annotations. The retained local
workbook still contains 16 recoverable threaded comments on its 2025–2027 budget
sheets; compatibility comment copies must not be counted twice. Current
application notes are visible and searchable, but BudgetLine has no annotation
field and exact assignment of those source comments to current records remains
unverified. A private recovery inventory is supplied outside the public repository.
This is a retained data-preservation gap, not completed migration acceptance.
The retired importer and old Piecework operational tooling are not restored.

## Independent statutory sources

- [HMRC K-code overriding limit](https://www.gov.uk/hmrc-internal-manuals/paye-manual/paye11095)
- [GOV.UK K tax codes](https://www.gov.uk/tax-codes/k-in-your-tax-code)
- [HMRC employer rates 2025–26](https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2025-to-2026)
- [GOV.UK employee NI categories and rates](https://www.gov.uk/national-insurance-rates-letters)

Regression coverage lives in `backend/tests/test_historical_payroll.py`, the
existing tax/goal/loan/seasonal suites, and the gift-planner frontend tests.
The historical probe baseline had ten failures and one unsupported-year control
pass. Integrated cutoff, seasonal scope, actual remaining, payee privacy, NI
category matrix and visible/searchable-note checks were also reproduced before
their final fixes. Verification results are recorded with the delivery evidence.

Canonical outbox transitions now include `payroll.generated`,
`payroll.reconciled`, `payslip.review_required`, `gift.purchased` and `gift.paid`.
They use the existing transactional outbox and a per-entity transition dedupe
key. Default HA payloads expose status/source/date only; private file paths,
names and amounts are absent. Stored ownership metadata preserves scope context.
An authenticated HA service may record overtime only for an employment paid into
a shared account; it does not impersonate a household member or access salary
and payslip detail.
