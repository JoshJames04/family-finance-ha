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
- Historical "Piecework Co" (discontinued — **not recreated**; transactions kept)

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

- staged 528 records; 404 imported; 2,335 entities across 12 sheets
- rollback voided 90 transactions; re-commit re-mapped all 404 —
  idempotent
- defect flags raised by the detector: B ×1 (loan remaining-balance
  `#REF!` — reconstructed; awaits reviewer disposition in the queue)
- gate: `ready=False` until every flag is resolved via
  `/api/v1/migration/flags/{id}/resolve` — correct behaviour

Sign-off: _pending user parallel-run month_ · retirement date: _pending_
