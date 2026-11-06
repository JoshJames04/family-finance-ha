# ADR-0003: Money Representation

Status: Proposed
Date: 2026-11-06

## Context

Financial data must be exact: payroll deductions, balances and budgets cannot
drift on float error. UI is UK-only: GBP, `dd/MM/yyyy`.

## Options considered

- **Integer minor units (pence) internally, `Decimal` at boundaries
  (chosen):** exact storage, trivial sums; `Decimal` where ratios/rates are
  applied (tax, overtime multipliers) with explicit rounding policy
  (half-even at pence, documented per calculation).
- IEEE float everywhere: rejected — classic accumulation bugs.
- `Decimal` columns everywhere: workable but slower in aggregates and adds
  no benefit for stored amounts.

## Decision

A `Money` value type is the only way currency moves through the domain and
API. Raw `int`/`float`/`str` amounts are rejected at schema boundaries.

## Consequences

- One rounding policy module; tests pin every rounding rule.
- Multi-currency is explicitly out of scope; the type carries `currency` for
  future-proofing but GBP is asserted.

## Validation / revisit triggers

Never for v1.0 scope; revisit only if multi-currency becomes a requirement.
