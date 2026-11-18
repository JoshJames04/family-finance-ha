# Employer bonus entry and layout plan

**Goal:** Put well-spaced bonus management on employer configuration, with Add bonus beside New salary and one input selected by bonus type.

**Spec:** User requests of 18 November 2026; keep all bonus functionality in PR #540, separate from payroll engine PR #543.

**Design:** Reuse SalaryBonuses, existing bonus APIs and hardened shared payroll automation. New entries choose salary percentage, value per share, or manual gross amount; only that value input is visible and submitted. Multiple entries still add within an employer/month. Employer share-payment configuration and effective-dated holdings remain intact. Preserve older combined entries and their hidden components explicitly when editing, rather than silently dropping financial values.

**Review boundary:** Bonus PR follows the engine PR so its review diff is bonus-only. No production writes, merges or private source fixtures. Preserve the existing uncommitted YTD safety work before integration.

## Tasks

- [ ] Preserve and evaluate the existing YTD safety patch/tests; integrate the hardened engine without dropping those safeguards.
- [ ] Keep only bonus-required migrations in this PR, with one migration head after the engine migration.
- [ ] Add failing UI tests for Add bonus placement, type-dependent fields, switching type without hidden values, existing combined-entry preservation, validation and month aggregation.
- [ ] Move bonus controls to employer configuration; remove bulky repeated bonus sections from Breakdown and use existing padding/spacing tokens.
- [ ] Wire bonus changes into shared automatic forecast refresh while retaining immutable basis snapshots, non-cash/outside-payroll distinctions and protected actuals.
- [ ] Run full backend/frontend and migration checks; update PR #540, closing tags and Josh's review request. Verify the combined local preview.
