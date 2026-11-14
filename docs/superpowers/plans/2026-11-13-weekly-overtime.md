# Weekly Overtime Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task by task, with independent task and final review.

**Goal:** Deliver #487 and a verified local preview of historical weekly overtime sheets and atomic weekly actions.
**Architecture:** Derive week groups from existing dates. Reuse per-entry lifecycle and payroll semantics; add one snapshot-checked bulk endpoint. No persisted week entity.
**Tech Stack:** Existing FastAPI/SQLAlchemy/Pydantic and React/TypeScript/Radix/Vitest/Playwright. No new dependency or database migration.
**Spec:** [weekly-overtime](../specs/2026-11-13-weekly-overtime.md).

## Global constraints

- Monday–Sunday, ISO week-year, historical grouping without record rewrites.
- Draft → submitted; submitted → approved/rejected; retain individual actions and payroll treatment.
- All intended entries, status/content snapshot, atomic mutation/audits and existing access controls.
- No live writes, manager messages, new dependencies or migration.
- Publish bot-only, PR into develop, JoshJames04 reviewer/assignee, clean CI/native review; no merge or release.

## Review focus

1. December/January ISO year and DST arithmetic, including historical dates.
2. Mixed status, partial eligibility and edits while confirmation is open.
3. Shared DB concurrent/replayed requests without partial mutations/audit duplicates.
4. Cross-employment/household/service/viewer scope.
5. Payroll cutoffs, approved-only feed and immutable generated snapshots.

### Task 1: Atomic weekly backend workflow

**Files:** domain/overtime.py, api/v1/employments.py, api/schemas.py, persistence/models.py (computed revision only), tests/test_overtime_weekly.py; generated frontend API types by root.
**Consumes:** existing scoped employment router and per-entry transitions/rate snapshots.
**Produces:** the exact POST/revision/response contract in the spec, with Monday validation, stale snapshot checks, conditional status update and atomic audits.

- [x] Write and run failing boundary/status/snapshot/authorization/payroll tests.
- [x] Implement the smallest shared domain/API changes and opaque content revision; no data migration.
- [x] Verify tests, Ruff/Mypy; independently review task quality and spec compliance.

### Task 2: Historical weekly presentation and actions

**Files:** frontend/src/pages/Overtime.tsx, lib/overtimeWeeks.ts, __tests__/overtime-weekly.test.tsx; existing overtime tests where status text now appears in headers.
**Consumes:** OvertimeOut.revision and Task 1 POST contract.
**Produces:** week groups, totals/status summaries and snapshot-confirmed bulk actions with busy/conflict recovery, preserving all row operations.

- [x] Write failing ISO-week grouping and workflow/component tests.
- [x] Use UTC calendar arithmetic and existing components; show all existing records grouped without writes.
- [x] Validate typecheck/full unit suite/build, responsive layout and independent task review.

### Task 3: Real API/browser integration and delivery

**Files:** frontend/e2e/overtime-weekly-real-api.spec.ts, README, affected docs, verification report outside repo.
**Consumes:** completed Tasks 1–2.
**Produces:** tested local preview and ready PR closing #487.

- [x] Verify synthetic real API/browser week submit, individual exception, bulk outcomes, added draft and mobile layout; inspect screenshots.
- [ ] Run full backend/frontend, perf, migrations/backup/API-generation/security/version/docs checks in required CI. Fix findings and repeat native review until clean.
- [ ] Back up local test data, restart the existing family-finance-ha backend only when ready, verify historical grouping read-only and preserve financial data. Stop disposable test containers.
- [ ] Commit/push as bot, open ready PR into develop, request reviewer/assignee, record issue/project status within bot permissions; no merge/deployment to live.

## Preflight rulings

The user explicitly approved issue #487 and requested completion; no further design/plan approval is needed. Existing linked worktree is reused on feature/487-weekly-overtime from current origin/develop. Existing source mounts remain stable. Group state is derived from entries; no separate week status or persisted week table. Router-level authorize_employment is mandatory for the new endpoint. GitHub project access remains App-denied and the user forbids human fallback; record issue comments if still blocked. Full verification evidence belongs to the delivery PR and report, so final CI can be recorded without another code commit.

Final delivery and CI status are recorded on the PR and the user-facing verification report; this plan records the implementation checkpoint without requiring a new code commit after CI finishes.
