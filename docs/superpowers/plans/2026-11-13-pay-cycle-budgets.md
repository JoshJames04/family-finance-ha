# Pay-cycle Budget Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement and independently review these tasks.

**Goal:** Implement #482–#484 with historical personal pay cycles, complete income detail and calendar household funding.

**Architecture:** Derive effective reporting windows separately from stored monthly plan dates. Reuse existing actuals calculations with explicit bounds, and add scoped funding detail/stock. Frontend personal views opt into pay cycles while Shared/All stay calendar based.

**Tech Stack:** Existing Python/FastAPI/SQLAlchemy and React/TypeScript; no new dependencies or schema migration.

**Spec:** `docs/superpowers/specs/2026-11-13-pay-cycle-budgets.md`.

## Global constraints

Integer pence; read-only GETs; no live writes; no real financial data in public artifacts; preserve plan amounts/status/locks and transaction dates; publishing through orbit-codex-bot only.

## Review focus

Cross-month payday adjustments; historical source ambiguity; mismatched personal/calendar totals; split/refund and salary-match double counting; contribution attribution leaking private accounts.

### Task 1: Payday period service (#482)

Files: new `backend/app/domain/budget_periods.py`, `backend/tests/test_budget_periods.py`.

Interface: `resolve_period(session, budget, *, basis="calendar") -> BudgetWindow`; dataclass fields `start`, `end`, `basis`, `employment_id`, `warning`. Source options and explicit anchor selection stay scoped to household/member.

- [x] Write and observe failing period/source/authorization tests.
- [x] Implement using existing `pay_calendar.parse_rule` and `paydays.pay_date`, including member fixed-day settings without employment.
- [x] Verify historical, shifted, ambiguous, custom-period and no-write cases.

### Task 2: Income/funding service (#483/#484)

Files: new `backend/app/domain/budget_funding.py`, `backend/tests/test_budget_funding.py`; stock-only aggregate allowlist if required.

Interface: `budget_funding(session, budget, account_ids, *, start, end, include_private=False) -> dict`. Return `opening_funds_pence`, `external_income_pence`, `income_items`, `contributions_pence`, `contribution_items`, `unattributed_internal_pence`. Item fields: transaction_id, date, account_id, payee, category_id, amount_pence, contributor_profile_id (nullable).

- [x] Observe failing income/stock/verified-contribution tests.
- [x] Reuse effective external/internal legs and confirmed transfer/match relationships; preserve privacy and stock semantics.
- [x] Verify totals reconcile in integer pence across scopes, splits, voids/refunds and month boundaries.

### Task 3: API integration and contracts

Files: `backend/app/api/schemas.py`, `backend/app/api/v1/budgets.py`, `backend/app/domain/budgets.py`, new API tests, generated frontend schema.

- [x] Add explicit optional start/end bounds to the three Budget domain calculations without mutating stored rows.
- [x] Add typed period/funding output and basis query; include calendar funding in All; validate anchor-setting writes with owner/admin gates and audit.
- [x] Observe route-level RED/GREEN, scope and no-write tests. Regenerate OpenAPI types and run Ruff/Mypy.

### Task 4: Budget UI

Files: `frontend/src/api/hooks.ts`, `frontend/src/pages/Budget.tsx`, focused unit/browser tests.

- [x] Add personal-only basis requests, exact effective date labels, source configuration warnings/selection and effective expected-movement ranges.
- [x] Add opening funds and complete received-income/contribution details outside the manual plan, retaining forecast distinction; actual details open by default.
- [x] Prove historical switching, headline reconciliation, scope separation, all state variants and mobile behavior.

### Task 5: Delivery

- [x] Independent review each task and final integrated branch; fix meaningful findings.
- [ ] Run full backend/frontend and real Docker browser checks, migrations/version/docs/security gates through CI.
- [ ] Update repository docs and issues; create ready PR into develop with Closes #482/#483/#484, request JoshJames04 and native review; repeat until clean.
- [ ] Leave live untouched. Project board requires bot access; record intended status if integration rejects it.

## Execution rulings

- Known historical employment periods retain their payday rules when a current primary employment is selected. Independent boundary resolution preserves continuity through job changes.
- A fixed per-member monthly payday supports incomplete employment setup; it creates no job, salary or forecast.
- #485 extends the shared economic-leg classifier to verified manual contribution portions. Calendar report dates/API shapes are preserved, while Budget/reports/analytics/forecast use the same corrected income/expense classification. This replaces an intermediate Budget-only adjustment rather than leaving disagreeing totals.
- Private Budget visibility uses the common RBAC policy, including users demoted to shared-only viewer access.
- Final CI/review evidence and publication status are recorded on the delivery PR; no merge or live deployment is part of this implementation.
