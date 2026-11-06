# Architecture Decision Records

ADRs record significant decisions: context, options, decision, consequences.
One ADR per decision; immutable once accepted — supersede with a new ADR.

| ADR | Decision | Status |
|---|---|---|
| [0000](0000-template.md) | ADR template | — |
| [0001](0001-technology-stack.md) | React/TS + FastAPI + SQLite stack | Proposed |
| [0002](0002-ha-delivery-model.md) | HAOS app (add-on) primary, container secondary | Proposed |
| [0003](0003-money-representation.md) | Integer pence + Decimal boundaries, GBP, dd/MM/yyyy | Proposed |
| [0004](0004-transfer-model.md) | Linked double-leg transfers, never income/expense | Proposed |
| [0005](0005-auth-model.md) | HA authN + app-level household RBAC | Proposed |
| [0006](0006-migration-provenance.md) | Staged, idempotent, reconciled Excel import | Proposed |
| [0007](0007-budget-value-model.md) | Original/revised/actual + variance + locking | Proposed |
| [0008](0008-starling-constraints.md) | Read-only, least-privilege, optional integration | Proposed |

## Template (ADR-0000)

```markdown
# ADR-NNNN: Title
Status: Proposed | Accepted | Superseded by ADR-XXXX
Date: YYYY-MM-DD

## Context
## Options considered
## Decision
## Consequences
## Validation / revisit triggers
```
