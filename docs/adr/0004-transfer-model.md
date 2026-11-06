# ADR-0004: Transfer & Money-Movement Model

Status: Proposed
Date: 2026-11-06

## Context

Household finances are full of internal movement (personal→bills,
personal→joint, joint→food). Treating legs as spend/income double-counts.
Full double-entry accounting UX is overkill for the audience.

## Options considered

- **Linked double-leg transfers (chosen):** one `transfer` entity, two
  transaction legs (out/in), invariant: legs net to zero; reports exclude
  transfer legs from income/expense by construction.
- Category-flag approach (`is_transfer`): error-prone, easy to miscount.
- Full ledger/journal: correct but heavy schema + UX for marginal gain at
  household scale.

## Decision

Transfers are first-class objects with reconciliation state; unmatched legs
(one side seen, other pending) are representable. Debts/repayments/savings
allocations are modelled as transfers to liability/goal-linked accounts or
dedicated repayment records — never as "expenses".

## Consequences

- Reports, forecasts and budgets share one exclusion rule — single source of
  truth for "is this real spend".
- Import pipeline maps workbook transfer rows onto legs; unmatched history
  is representable.

## Validation / revisit triggers

Revisit only if multi-entity bookkeeping (e.g. rental property accounts) is
ever required.
