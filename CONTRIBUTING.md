# Contributing & Engineering Standards

## 1. Workflow

- `main` is the protected release branch. All work lands via PR from
  `feature/<issue>-<slug>`, `fix/<issue>-<slug>` or `spike/<issue>-<slug>`.
- Every PR references its issue (`Closes #123`) and uses the PR template.
- Squash-merge to keep `main` history readable; PR title = commit summary
  (conventional-commit style, e.g. `feat(budgets): …`).
- Small, reviewable PRs — an issue may land across several PRs; keep issues
  open until their full acceptance criteria are met.

## 2. Definition of Ready

An issue is `ready-for-dev` when:

- Scope and behaviour are unambiguous; user stories use the
  *As a… I want… so that…* format with Given/When/Then acceptance criteria.
- Dependencies are linked and resolved or explicitly non-blocking.
- Edge cases and testing requirements are listed.
- Priority, complexity estimate, area labels, epic and milestone are set.
- Any required design/ADR/spike output exists or is part of the issue.

## 3. Definition of Done

A PR may merge when:

- All acceptance criteria are demonstrably met.
- Tests: new/changed behaviour is covered; financial calculations have
  deterministic unit tests; full suite green in CI.
- Money handling uses the shared money type — no raw floats for currency;
  UK locale, `dd/MM/yyyy` in UI.
- Migrations include tested upgrade + downgrade paths where schema changed.
- Audit/provenance rules respected: financial mutations audited; imported
  data keeps provenance.
- Security: no secrets, no real financial data; authz checked at API level.
- Docs updated (README/docs/ADR) where behaviour or decisions changed.
- Reviewed by a maintainer; CI gates pass (lint, typecheck, tests, secret
  scan).

## 4. Labels

**Type:** `epic`, `user-story`, `task`, `bug`, `spike`, `documentation`,
`security`, `testing`
**Priority:** `priority:p0` (foundation/data-safety, blocks v1.0) ·
`priority:p1` (essential agreed scope) · `priority:p2` (advanced, v1.0 scope) ·
`priority:p3` (future)
**Area:** `area:frontend`, `area:backend`, `area:database`,
`area:home-assistant`, `area:banking`, `area:payroll`, `area:migration`,
`area:forecasting`, `area:security`, `area:devops`
**Readiness:** `needs-design`, `ready-for-dev`, `blocked`

Default GitHub labels (e.g. `wontfix`) remain for general use.

## 5. Engineering standards

- **Type safety:** TypeScript strict mode; Python type-checked (mypy/pyright
  strict-ish on domain modules).
- **Validation:** all API I/O schema-validated; domain invariants enforced
  in the model, not just the API.
- **Determinism:** payroll/forecast/budget calculations are pure functions
  of inputs + versioned parameter sets — reproducible in tests.
- **Tests:** pyramid = unit ≫ integration > e2e; financial edge cases named
  in Epic 17 are mandatory coverage.
- **Migrations:** Alembic; never edit released migrations; data migrations
  are transactional and reversible where feasible.
- **Commits:** imperative, scoped, reference issues; no secrets, ever.
- **Docs:** ADRs for every architectural decision; keep docs in the same PR
  as the change.

## 6. Spikes

Spikes are time-boxed investigations with a written outcome (comment on the
issue + ADR/doc update if a decision results). A spike does not ship code to
`main` except thin proof scaffolds behind flags.
