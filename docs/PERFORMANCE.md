# Performance Budgets and Validation (issue #132)

**Status:** Measured — initial budgets set and verified at fixture scale.

## 1. Budgets

User-facing hot paths on HAOS-class hardware (single-container, SQLite,
~5 years of ledger data ≈ 50k transactions):

| Path | Budget | Measured (median, 50k rows) |
|---|---|---|
| Transaction list — first page (50 rows) | 200 ms | 44.5 ms |
| Payee search (`q` filter) | 300 ms | 3.2 ms |
| Category filter | 200 ms | 0.9 ms |
| Dashboard bundle (accounts + 90d expected movements + 30d projection) | 2 s | 15.6 ms |
| Forecast recompute (90d projection) | 1 s | 10.5 ms |

Measured on a mid-range Windows dev machine (2026-11), SQLite, Python
3.13. HAOS hardware is slower; budgets carry ~5–20× headroom so the
shape of each check stays the signal, not the absolute number.

## 2. Method

`tools/perf_check.py` seeds a deterministic ~50k-row ledger (5 years,
3 accounts, 30 categories, ~200 payees, 400 expected movements) into a
fresh migrated database, then times each path at the domain/query layer
with `perf_counter`, median of 3 repeats. The same seed+measure code is
exercised by `backend/tests/test_perf.py` (15k rows) so drift in the
measurement code itself fails the suite.

## 3. Running it

```sh
python tools/perf_check.py            # report, exit 0
python tools/perf_check.py --strict   # exit 2 if any check >3× budget
pytest -m perf backend/tests          # smaller in-suite run
```

CI reports the full run every push — "Perf report" step in
`tests.yml`, `continue-on-error` (non-gating per the issue; tighten to
`--strict` once a HAOS-class number exists).

## 4. Notes / exceptions

- No path currently exceeds budget — the headroom is intentional, not a
  licence to regress. Watch `txn_list_page1` (nearest the line at 0.22×
  of the absolute budget it is also the only check >0.1×).
- Endpoint-level timing is not measured here — FastAPI serialization
  adds a fixed per-request cost that is hardware/transport-dependent;
  the domain layer is where scaling risk lives.
