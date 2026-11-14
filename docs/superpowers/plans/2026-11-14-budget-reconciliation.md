# Budget Reconciliation Implementation Plan

**Goal:** Fulfil remaining #483 and #466 code acceptance.

**Architecture:** Reuse scoped calendar actuals for optional-plan sections;
derive income reconciliation from the authoritative income items. Keep the
combined account-union actuals independent of sections.

**Tech stack:** FastAPI/SQLAlchemy, React/TypeScript, pytest/Vitest.

**Spec:** `docs/superpowers/specs/2026-11-14-budget-reconciliation.md`.

- [x] Write API regressions for visible/calendar/missing/zero-plan sections and
  UI regressions for links, exact income subtotals and read-only sections; RED.
- [x] Add schemas/sections, regenerate OpenAPI types and implement the two UI
  pieces using existing components; GREEN.
- [x] Run focused backend suites, Ruff/mypy, frontend typecheck/tests/build,
  update owning docs and commit as the bot. Parent reviews/publishes.

Backend verification uses only `/tmp/fix483/backend` and disposable SQLite
fixtures in the existing interpreter. Never open application `/data` or restart
the active server. Frontend verification uses the existing dependency junction.

Verification: 71 focused backend tests, Ruff and mypy passed. Frontend typecheck,
211 tests with two workers and production build passed. Synthetic browser checks
passed at 375px and 1280px, including real navigation to selected transactions,
calendar sections and no viewport overflow/page errors. The initial unbounded
frontend run had one existing transaction bulk-selection timeout; that file passed
alone and the full bounded-worker run passed. Parent owns full CI/publication.

- [x] Root review: make All income coverage owner-specific using section
  transaction/category keys; prove planned/unplanned/mixed categories with
  Shared, missing plans and repeated split categories. Preserve legacy fallback.
- [x] Follow-up verification: 214 frontend tests and four synthetic browser tests
  passed; production build passed. Visually inspect and commit synthetic desktop
  and mobile screenshots under `docs/images` for PR evidence.

- [x] Native PR #498 review: union plan categories with signed as-of spending
  and income items in each section, including missing plans and uncategorised
  activity. Distinguish absent plans from zero and retain income/expense sides.
  Three regressions failed before the fix; 217 frontend tests, TypeScript/build
  and four desktop/mobile browser checks passed afterward. Refresh synthetic
  screenshots showing unplanned/refund/split categories and exact side totals.
