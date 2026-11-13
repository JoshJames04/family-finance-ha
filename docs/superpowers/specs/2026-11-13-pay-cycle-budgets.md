# Personal pay-cycle and household funding views

Approved requirements: issues #482, #483 and #484, following Josh's 13 November discussion and instruction to implement.

Personal monthly plan labels remain nominal calendar months for navigation and plan storage. The November plan uses the adjusted October payday through the day before the adjusted November payday. Use existing payday rules, including planned early weekend/holiday payments; an unexpected bank posting date never moves the window. All historical plans use this interpretation without rewriting transaction dates, approved amounts, revisions or locks. Non-monthly custom plans keep their explicit dates.

Shared and All remain calendar-month views. All computes actuals from its authorized account union, not personal cycle totals. Shared includes shared/joint accounts only and displays verified personal-to-shared contributions as its main funding measure; other external income remains separately visible. Shared-to-shared space movements are not new contributions. Unknown source attribution remains explicit.

Every received external income leg must appear in income detail even without a planned category. Forecasts show only outstanding expected pay and remain distinct from received cash. Opening funds are the authorized ledger stock strictly before the displayed window, including opening transactions and internal legs; they are separate from income and do not revise the spending plan.

## Implementation decisions

- Add opt-in `basis=pay_cycle` to existing single-budget detail/actuals endpoints. Their default calendar contract and All endpoint remain compatible with existing report/dashboard consumers. The personal Budget page opts in; Shared/All request calendar views.
- Return a `period` object with effective start/end, basis, anchor source and any configuration warning. BudgetOut's stored dates stay unchanged. All dates on the page, expected movements, bills, reconciliations and drills use the effective period.
- Reuse existing employment payday parsing. An unambiguous monthly source is automatic. Allow explicit per-member employment selection using scoped AppMeta state. Ambiguous/invalid/missing sources produce a visible calendar fallback warning, never invented paydays. Historical unique employment coverage is preferred; an explicitly selected/current sole monthly rule can interpret older legacy history.
- Derived windows are read-only. Existing original plan/revision amounts are reused whole, not prorated or copied. GET performs no writes; no database migration is required.
- Fixed monthly member paydays can be configured before employment exists. They use the same Friday weekend adjustment rule, are scoped per member, and do not invent employment or salary. Manual and employment selections clear each other; Automatic clears both. Explicit employment selection does not replace a unique former job's known payday history outside its own employment span.
- Shared contribution detail uses confirmed transfer boundaries or confirmed expected-contribution links, not payee guesses. Keep economic external income separate, and net contribution withdrawals transparently where applicable. Account visibility must not be widened for attribution.
- Preserve integer-pence arithmetic, split/refund/void rules, real transaction dates, existing lifecycle protection and the original calendar report APIs. Do not change live data or bank configuration.

## Acceptance

Test historical October/November windows, Saturday/Sunday and holiday shifts, leap/year boundaries, no gaps/overlaps, missing/ambiguous rules, multiple jobs, retained locks/amounts and GET read-only behavior. Verify income outside the plan, split receipts, refunds/internal movement exclusion, contribution attribution and shared-space exclusion, negative/zero opening stock and all resource roles. Browser proof covers switching owners/periods, exact dates, forecast matching, income/headline reconciliation and mobile states. Deliver a non-draft bot-authored PR into develop for JoshJames04, full CI and clean native Codex review; do not merge or deploy.
