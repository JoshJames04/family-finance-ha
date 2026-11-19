# Excel Migration Strategy — `Family Budget.xlsx`

**Status:** Historical record — the workbook was retired 2026-11-09 and
the one-shot migration machinery (extractors, `import_*`/`migration_flag`
tables, `/api/v1/migration/*` routes) has since been removed. This
document remains as evidence of the migration strategy and retirement
gate; the commands and endpoints below no longer exist.

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

`tools/run_migration.py` executed this gate end-to-end locally
(one-shot tooling — removed after workbook retirement):
backup → stage → commit → reconcile → rollback test → re-commit →
retirement gate. Run against the real workbook (local only, counts
never values):

- staged 529 records; 405 imported; 2,345 entities across 12 sheets
- rollback voided 90 transactions; re-commit re-mapped all 405 —
  idempotent
- `tools/reconcile_parity.py` (since removed) ran against the imported DB: gifts,
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

### Historical annotation preservation

Settings → Historical notes shows the active administrator's private archive.
Search covers full note text, original year, sheet, cell and timestamp. Text is
retained without truncation or interpretation. Missing original source evidence
is displayed as **retained extraction, source workbook not reverified**. This is
preservation of a retained copy, not evidence of a verified workbook import.

Before importing, back up the target, verify the backup, and rehearse with an
isolated copy. Never commit source material, an import manifest or response bodies
to the repository or CI. Keep all source identity and mapping decisions private.

`PUT /api/v1/historical-notes` accepts a JSON object containing `source_sha256`
(the retained source hash, not a fresh verification), `provenance_status` set to
`retained_extraction_unverified`, and `notes`. Each note requires `source_key`,
`sheet_index`, `sheet_name`, `year`, `worksheet_part`, `cell_reference` and `text`;
`thread_id` and `timestamp` may be null. No current-record mappings are accepted.
Use the intended household and authenticated administrator profile. Validation
rejects duplicates, unknown fields, blank text and out-of-bounds values before
writing. Limits: 100 notes, 100,000 characters per note, 2 MB request body.
The body limit is enforced while streaming, including uploads without a content
length. OpenAPI publishes the JSON manifest contract; invalid inputs still return
only a generic validation error without echoing note content.

An identical repeat returns the same digest without changing rows. Different
content cannot overwrite an existing archive. Keep the returned digest privately:
`DELETE /api/v1/historical-notes?digest=...` removes only that owner's unchanged
archive and conflicts on a stale digest. Re-import only after an intentional
rollback. Chunks in existing `app_meta` storage respect its 1,024-character value
limit and commit atomically; no schema migration is needed. Backups include them.
Import and rollback emit audit events in the same transaction, identifying the
actor and owning profile without retaining note text, source metadata or archive
digests in the shared audit trail. Identical imports do not create extra events.

After rehearsal, compare every source key and full text, context and provenance
against the approved retained inventory. Verify the archive in the app, repeat
the import, rehearse rollback, and restore the archive. Production preservation
and independently verified current-record mappings remain separate sign-off work.

## Legacy bank mapping category constraints (#550, 1.5.2)

Some older SQLite installations have a nullable
`bank_account_mapping.external_category_uid` without the empty-string server
default, despite a migration ledger beyond `0050`. A ledger check alone cannot
detect this drift. SQLite permits duplicate NULL keys under a UNIQUE constraint;
discovery uses an empty category string, so an old NULL mapping can also be missed
on rediscovery. The model and current discovery writer already use empty strings.
The original cause of the divergent legacy schema has not been established;
this maintenance path does not rewrite migration 0050 or stamp the ledger.

From the installed backend environment, diagnose an existing database explicitly:

```sh
python -m app.persistence.bank_mapping_repair /path/to/finance.db
```

This opens SQLite read-only and reports `nullable`, `server_default`,
`needs_repair`, `null_categories`, `collision_groups` and `association_conflicts`.
The output contains counts, not provider identifiers or financial rows. A missing
file is an error; it does not create a database. The command is separate from app
startup and ordinary upgrades. It is not a live repair authorisation.

For collisions, inspect the following queries **locally and privately**. Do not
publish results containing household or account identifiers. Review the original
bank link and confirmed destination; the repair never chooses a surviving row,
merges mappings, changes ownership or infers an account association.

```sql
SELECT GROUP_CONCAT(id) AS mapping_ids, COUNT(*) AS conflicting_rows
FROM bank_account_mapping
GROUP BY connection_id, external_uid, COALESCE(external_category_uid, '')
HAVING COUNT(*) > 1;

SELECT m.id AS mapping_id, m.household_id, m.connection_id, m.account_id
FROM bank_account_mapping m
LEFT JOIN bank_connection c ON c.id = m.connection_id
LEFT JOIN account a ON a.id = m.account_id
WHERE c.id IS NULL OR c.household_id != m.household_id
   OR (m.account_id IS NOT NULL AND
       (a.id IS NULL OR a.household_id != m.household_id))
   OR (m.status = 'linked' AND m.account_id IS NULL);
```

Rehearse on a disposable copy first. After authorisation for the target, stop the
app and workers, retain a separate verified backup, and explicitly request repair:

```sh
python -m app.persistence.bank_mapping_repair /path/to/finance.db \
  --repair --backup-dir /path/to/backups
```

The repair reserves SQLite's writer lock before checking data and keeps that lock
through backup and commit. It uses an independent read connection to create the
standard backup snapshot and verifies its integrity before changing any rows.
All duplicate normalised keys, including duplicate NULL keys with matching
destinations, and conflicting/missing associations block repair. Unexpected unique
keys, mapping triggers, unnamed mapping checks, expression/descending indexes,
custom column/index collations, AUTOINCREMENT/STRICT/WITHOUT ROWID table options
or existing foreign-key violations also block rebuilding. These definitions,
including AUTOINCREMENT's retained ID high-water mark, may not survive schema
reflection faithfully. The raw DDL token checks are deliberately conservative.
The table must have exactly the supported three-column unique constraint;
additional unique constraints are refused before normalisation, including
`ON CONFLICT REPLACE` constraints that could otherwise delete rows. Ordinary
supported indexes remain intact. Investigate these cases; do not delete mappings
or constraints merely to make the repair pass.

Only unambiguous NULL category values become empty strings. The table is rebuilt
with `NOT NULL DEFAULT ''`, retaining its existing unique key, indexes and foreign
keys. Mapping IDs, account links, other columns, related rows, transaction history
and the migration ledger are retained. A correct schema needs no repair or backup.
Post-repair mapping row-count, schema, foreign-key and database-integrity checks
run before commit. Any unexpected mapping row loss rolls back the repair.

Exceptions roll back both schema and data; a process interruption before commit
recovers through SQLite's transaction journal. Keep any backup produced by a
failed attempt. Rerun the diagnostic before retrying. For an intentional rollback,
stop all database clients and restore the verified pre-repair snapshot using the
existing backup restore procedure (including removal of stale WAL/SHM files).
Confirm integrity, associations and ledger before restarting; restoring also
returns the legacy constraint difference. Re-run the diagnostic after a successful
repair and rehearse discovery on the disposable copy before operational sign-off.
