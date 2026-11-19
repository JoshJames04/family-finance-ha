# Overview salary account implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Resolve the Overview account from the signed-in member's current salary setup and keep both projection widgets aligned.

**Architecture:** A Dashboard-scoped account context resolves identity, existing employment and account reads once, and owns temporary manual selection. Both projection widgets consume that selection. Add an explicit opt-out from useProjection's existing first-account fallback; retain that fallback for other callers.

**Tech stack:** Existing React, TypeScript, Vitest, Testing Library and API client.

**Spec:** ../specs/2026-11-19-overview-salary-account-design.md

## Constraints and review focus

- No new dependency, schema migration or household-data modification.
- HA identity must override a stale browser profile; never use another member's job as a default.
- No first-account projection while employment/identity are unresolved or failed.
- Deduplicate salary accounts and exclude inactive, future/ended or inaccessible destinations.
- Manual selection must update both widgets and cannot leak after an identity change.
- Keep errors retryable and distinguish manual selection from missing accounts.

## Task 1: Regression coverage

- [ ] Run the existing frontend suite as a clean baseline.
- [ ] Add dashboard-account.test.tsx using synthetic API responses.
- [ ] Prove the signed-in member defaults to their salary account even when another account is first; prove HA identity beats stored profile.
- [ ] Cover ambiguous/missing accounts, identical destinations, inactive/dates, deferred/error reads, manual switching, remount and identity changes.
- [ ] Run the new cases against unchanged code and record the expected failures.

## Task 2: Minimal shared account selection

- [ ] Add widgets/DashboardAccount.tsx with account state, scoped identity/employment reads and a shared selector.
- [ ] Mount its provider around Dashboard widgets.
- [ ] Add useProjection(accountId, {autoSelect}) so autoSelect:false never guesses an account.
- [ ] Consume the context in Safe to spend and Balance forecast, retaining existing widget-only caller compatibility.
- [ ] Keep the existing dropdown styles, add a genuine choice placeholder and show loading/error/choice states accurately.
- [ ] Run focused tests to green and update affected existing expectations only where the agreed behaviour changed.

## Task 3: Verify and deliver

- [ ] Run the full frontend suite, typecheck, build and audit; update Dashboard documentation.
- [ ] Commit/push through Orbit, open a non-draft PR into develop with Closes #544.
- [ ] Review fresh Codex feedback and all required CI on the final commit before requesting Josh's review.
- [ ] Apply only the verified code to the combined local preview, preserving its data and other pending features; verify the rendered widget and API account IDs.
- [ ] Keep issue/project status and concise verification evidence current.
