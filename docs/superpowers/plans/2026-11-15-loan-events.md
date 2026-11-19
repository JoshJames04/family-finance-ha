# Loan balance events implementation plan

**Goal:** Deliver the canonical loan balance/fully-repaid subset of #432.
**Spec:** #432 and [ADR-0009](../../adr/0009-ha-automation-v2.md).
**Architecture:** Reuse transactional snapshot/outbox state, existing derived
loan outstanding, household/owner feed filtering and HA family streams. Seed
existing state silently; record later committed balance transitions. No migration,
new balance field, raw bank-import inference or repayment due-date inference.

- [x] Add failing tests for initial seeding, repayment to zero, deletion/re-cross,
  future-dated repayment, scope change, safe detail, household isolation and rollback.
- [x] Add a single-loan evaluator and scheduled household hook; seed before and
  evaluate after existing loan/repayment writes so changes between ticks survive.
- [x] Add a Loans event family and test native stream routing.
- [x] Document exact event payload, privacy, replay semantics and missing due dates.
- [ ] Run focused tests, backend suite, lint/typecheck and HA tests; review then PR
  into develop with a focused child closing tag and reference #432.
