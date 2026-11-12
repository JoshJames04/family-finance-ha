# Workbook → app parity traceability matrix (issue #387)

**Status:** Historical record — written while the migration machinery
existed. Extractor names, `/migration/*` routes and `test_*extractor`
files below have since been removed with the retired pipeline; they are
kept here as evidence of the parity verification performed.

Every workbook area traced to its app equivalent — backend domain,
API surface, frontend screen, covering test — with a parity verdict.
Sheet names use the pseudonymised aliases that were generated for
the structural inventory (issue #386, inventory removed with the
one-shot tooling after retirement); the real-name map remains
`data/migration-map.local.json` (gitignored).

Verdicts: **PASS** (parity exercised by tests) · **GAP** (feature
missing or unverified) · **DIFF** (intentional difference, approved) ·
**DATA** (import path exists; numeric reconciliation pending — #389).

## Yearly budget sheets — `Member - 2024/25/26/27`

Tables per year: `tblIncome` (income rows), `tblPersonal`/`tblDaily`
(owner lines), `tblHouse`/`tblShared`/`tblOther` (shared lines),
`tblTotals` (skipped — derived).

| Workbook area | Backend | API | UI | Test | Verdict |
|---|---|---|---|---|---|
| `tblIncome` rows | `migration.IncomeExtractor` → `Transaction`/`Budget` | `POST /migration/commit` | Import wizard | `test_budget_extractor`, `test_migration_harness` | DATA — import proven; per-year totals reconcile tracked in #389 |
| `tblPersonal`/`tblDaily` (owner budget lines) | `BudgetYearExtractor` → `BudgetLine` | `GET /budgets`, `/commitments` | Budget page | `test_budget_extractor`, `test_budgets`, `test_manual_parity` | DATA — same as above |
| `tblHouse`/`tblShared`/`tblOther` | `BudgetYearExtractor` → `BudgetLine` (shared) | `GET /budgets` | Budget + Shared | `test_budget_extractor` | DATA |
| `piecework*` `SUMIFS` refs on year sheets | piecework employment (Piecework Co delivery) | `/employments` delivery entries | Salary | `test_settled_piecework_api` | PASS — extractor resolves the SUMIFS targets |

## `Salary History`

| `tblEmploymentHistory` rows | `SalaryHistoryExtractor` → `Employment` + `SalaryRevision` (effective-dated) | `GET /employments` | Salary | `test_employment`, `test_payroll_extractors` | DATA — importer proven; golden payroll values verified in test_payroll_* |

## `Overtime`

| `tblOvertime` rows | `OvertimeExtractor` → `OvertimeEntry` (literal date/hours; stale 'Payroll Date' formula resolved via `_pay_date`) | `/employments/{id}/overtime` | Salary | `test_payroll_extractors` | PASS — attribution rule replicated, not read from stale cache |

## `Piecework Co` (hidden)

| `pieceworkIncomeTbl` daily totals | piecework `Employment` + `DeliveryEntry` rows (per-shift hourly base + delivery charges + tips) | `/employments` delivery entries | Salary | `test_settled_piecework_api`, `test_payroll_overrides` | DIFF — discontinued workflow by design; **historic records retained**, no new generation (#305) |

## `Monthly Model` (hidden)

| `tblMonthlyModel` rows | `MonthlyModelExtractor` → `PayrollRun` + `PayrollComponent` lines | `/payroll/runs` | Salary → payroll runs | `test_payroll_golden`, `test_payroll_totals` | DATA — importer proven; golden accuracy verified |

## `Tax Tables` (hidden)

| `tblPAYE_Bands` / `tblNI_Bands` / `tblStudentLoanPlans` | extractor → versioned `TaxRuleSet.params` JSON per tax year | (params feed `/payroll`) | — | `test_payroll_golden`, `test_payroll_totals` | DATA — rule-set versioned per `payroll_rule_set_year` |

## `FAMILY_LOAN_LEDGER` (hidden)

| loan ledger rows | extractor → `Loan` + `Repayment` rows | `GET /loans` | Loans | `test_loan_xmas_extractors`, `test_loans` | DATA — importer proven; one defect flag (`#REF!` remaining-balance, reconstructed) awaits disposition |

## `Xmas` (hidden)

| `xmasYYYY` tables | extractor → `SeasonalEvent(year, christmas)` + `Gift` rows | `/seasonal` | Gifts/Seasonal | `test_loan_xmas_extractors`, `test_seasonal` | DATA |

## `MEMBER2_BUDGET` (hidden)

| member budget sheet | member-scoped `Budget`/`BudgetLine` (`owner_profile_id`) | `/budgets` member scope | Budget | `test_visibility` | DATA — member scoping verified; sheet-level parity pending #388 |

## Cross-cutting

| Area | Coverage | Verdict |
|---|---|---|
| Comments/annotations | Historical importer counted comments but did not retain them. Current gift/event notes are visible; gift notes are searchable. | UNVERIFIED — missing original comments cannot be reconstructed from retired source evidence; see [finance audit disposition](FINANCE-HISTORICAL-AUDIT.md). |
| `LastCol`/`PrintArea_SET` defined names | `cached_values` reads Excel's last-calculated results — never recomputes formulas | PASS — design: read cached values, never recompute |
| Error cells (`#REF!`, `#DIV/0!`) | `inspect_workbook` reports counts; `defects.py` flags them in the review queue | PASS — B×1 flag pending reviewer (§7.1) |

## Outstanding numeric evidence

- #389 — per-year/per-account reconciliation report (workbook totals vs app totals).
- #390 — payroll breadth map: every workbook payroll path ↔ golden case.
- One defect flag (loan `#REF!`) pending reviewer disposition (§7.1).
- #391 — parallel-run + retirement sign-off (operator gate).
