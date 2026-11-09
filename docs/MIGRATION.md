# Excel Migration Strategy — `Family Budget.xlsx`

**Status:** Strategy — workbook inspection spike will produce the authoritative
structural inventory before import implementation.

## 1. Source inventory (assessed)

~12 worksheets, 7 hidden:

- Annual personal budgets 2024, 2025, 2026, 2027
- Overtime calculations
- Salary history
- Monthly payroll calculations
- Tax tables (PAYE/NI reference)
- Family loan / repayment tracking
- Christmas budgeting and gift planning
- Second household member's personal budget
- Historical "Slow Boat" (discontinued — **not recreated**; transactions kept)

Inspection must enumerate hidden/very-hidden sheets, formulas, comments,
data validation, named ranges and cross-sheet dependencies.

## 2. Pipeline

```
read-only parse → staging tables → interpret/classify → validate
    → commit (idempotent) → reconciliation report → review queue
```

- Source file opened read-only; file hash recorded per batch.
- Every imported row carries sheet/cell provenance, raw + interpreted value,
  classification and import timestamp.
- Re-running the same batch is a no-op (idempotency keys = provenance hash).
- `dry_run` produces the full report without committing.
- Rollback removes an entire batch atomically.

## 3. Classifications

| Class | Meaning |
|---|---|
| `actual` | Confirmed historical spend/income |
| `forecast_overwritten` | Forecast cell overwritten by an actual — imported as actual-with-flag, **never** presented as original forecast |
| `aggregate` | Sheet-level totals/subtotals — stored as aggregates, not synthesised into fake transactions |
| `unknown` | Ambiguous — routed to review queue |

Fabricating transactions to reconstruct aggregates is forbidden.

## 4. Double-counting prevention

- Imported aggregates and transactions carry period + account scope.
- When bank sync later covers an overlapping period, reconciliation links or
  explicitly de-duplicates; rules forbid summing imported aggregates with
  overlapping bank transactions.

## 5. Defect register

| ID | Defect | Resolution approach |
|---|---|---|
| A | Annual totals include monthly intermediates (2026/2027 house bills) | Recompute from line items; compare vs sheet value; report delta |
| B | `#REF!` in repayment remaining-balance | Rebuild balance as principal − Σ validated repayments; flag residual |
| C | Annual ≠ Σ monthly (older sheets) | Reconciliation check per year; report |
| D | Hardcoded PAYE/NI | Superseded by versioned tax engine (Epic 8); migration captures the reference tables as tax data |
| E | Overwritten forecasts | Classification `forecast_overwritten` |
| F | Out-of-range payroll periods | Boundary validation; opening balances captured |

Each defect has a dedicated issue with synthetic fixtures + acceptance tests.

## 6. Reconciliation report contents

- Per-sheet: rows read, imported, classified, flagged.
- Recomputed vs sheet totals for every annual budget (defects A/C).
- Loan balance reconstruction vs sheet (defect B).
- Period coverage map; gaps and overlaps.
- Review queue: unresolved items blocking workbook retirement.

## 7. Workbook retirement gate (v1.0 acceptance)

The workbook may be archived only when the end-to-end migration acceptance
scenario passes: clean batch + report, zero unresolved flags, spot-check
sign-off, and successful parallel-run month where app figures ≥ workbook
fidelity.

### §7.1 Acceptance run evidence (issue #56)

`tools/run_migration.py` executes this gate end-to-end locally:
backup → stage → commit → reconcile → rollback test → re-commit →
retirement gate. Run against the real workbook (local only, counts
never values):

- staged 529 records; 405 imported; 2,345 entities across 12 sheets
- rollback voided 90 transactions; re-commit re-mapped all 405 —
  idempotent
- `tools/reconcile_parity.py` run against the imported DB: gifts,
  overtime, payroll and all 169 budget rows reconcile to the penny
  (row/month level); zero untraceable transactions; loan ledger carries
  one documented difference (defect B)
- defect flags raised by the detector: B ×1 (loan remaining-balance
  `#REF!` in the amount column — detector reconstruction corroborated
  by the workbook's cached totals). Disposition `wont_fix`: the
  workbook cell cannot be repaired from the file; the ledger imports
  without the broken row and the residual is documented. If the
  payment was real, the owner enters it as a manual repayment in-app
  after migration — that is verifiable from bank records, not the file
- gate: `ready=True` once the flag is resolved via
  `/api/v1/migration/flags/{id}/resolve` — correct behaviour

Sign-off: approved by the household owner — parallel-run month waived
on reconcile evidence + live use · retirement date: 2026-11-09

### §7.2 Moving installs (snapshot → new install)

A `VACUUM INTO` snapshot carries the full database — but **not** the
`.vault_key` file in the data dir. On a new install a fresh key is
generated, so migrated bank credentials fail to decrypt: sync then
returns **422 "re-authenticate"** (never 500 — #349), and the fix is
`POST /api/v1/banking/connections/{cid}/rotate` with the PAT or a
fresh PAT.

- Restore uploads land under `backups/pending/` — they never appear
  in the backups list (#342), so only genuine backups are downloadable.
- Profile identities: restored profiles have `ha_user_id = NULL`; the
  first ingress login would auto-provision a duplicate member. Admin
  links the real HA user id via Settings → member → **Link** (or
  `PATCH /api/v1/profiles/{id}`), which also deactivates the duplicate
  (#340).
- `GET /api/v1/admin/backups` + `/download` →
  `POST /api/v1/admin/restore/upload` → restart; boot applies the
  pending restore before migrations open the engine.
