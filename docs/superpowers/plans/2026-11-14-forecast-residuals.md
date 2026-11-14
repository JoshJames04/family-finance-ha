# Forecast Residuals Implementation Plan

**Goal:** Correct #491 without changing stored financial data or API contracts.

**Architecture:** Reuse `contributions.status` for signed outstanding movement
rows. Share outstanding payday selection between projection and scenarios.

**Tech stack:** Python, SQLAlchemy, pytest, disposable SQLite.

**Spec:** `docs/superpowers/specs/2026-11-14-forecast-residuals.md`.

- [x] Add synthetic regressions in `backend/tests/test_projection_residuals.py`
  and payroll replacement assertions; observe the actual forecast failures.
- [x] Update `domain/projection.py` and `domain/scenario.py` using existing match
  status; run regressions and relevant projection/scenario/payroll/Budget suites.
- [x] Document residual semantics in README/architecture, check Ruff/mypy,
  inspect the diff, and commit only this change. Parent owns full CI/publishing.

Run tests from a copied backend under `/tmp/fix491/backend` in the existing
Python container with disposable test directories, never `/data`. Keep the
active server stable; do not install dependencies globally or restart it.

Evidence: the early-payment domain, public API and payroll-supersession regressions
failed against the original projection; the focused eight-file regression run
passed 132 tests after the correction. Ruff passed and mypy found no issues in
120 source files. Full CI and publishing remain the parent's responsibility.
