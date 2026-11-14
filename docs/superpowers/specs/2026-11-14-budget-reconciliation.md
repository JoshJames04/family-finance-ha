# Complete Budget income reconciliation and All sections

Received-income detail opens the actual transaction by ID and reconciles its
headline in integer pence: totals per category, income-plan category subtotal and
outside-income-plan subtotal (including uncategorised receipts). Membership uses
income lines only. Split legs keep their exact amounts/dates, manual plans and
outstanding salary remain separate, and contribution detail gets receipt links
without salary-category subtotals.

All retains the independent authorized account-union headline and existing merged
plan. Add owner-labelled read-only calendar sections for visible budgets and
authorized account owners, Shared once. A missing plan has null detail while its
scoped actuals remain visible; a genuine zero plan retains detail. Include exact
dates and per-category planned/received/spent figures so sections can reconcile
to individual calendar views without summing personal payday-cycle actuals.

Additive `BudgetCombined.sections` entries contain owner_profile_id, name,
optional detail and actuals. Existing detail/actuals remain compatible. Preserve
permissions, historical plans/ledger, financial classification and current
loading/error handling. No migrations, dependencies, live writes or restarts.
