# Roadmap — Family Finance

Milestones mirror GitHub
[Milestones](https://github.com/JoshJames04/family-finance-ha/milestones).
Phases are ordered by
dependency, not by feature desirability — see
[Prioritisation](#3-priorities) for the critical path.

## 1. Phases

### M1 — Architecture and Foundation
**Entry:** backlog created, ADRs drafted.
**Exit:** app skeleton boots in a dev HA environment; CI green; DB +
migrations + config + secrets + logging + health checks working; auth spike
decided.
Covers: Epic 1, Epic 2 (spike + model), Epic 16 (design spike + scaffold).

### M2 — Financial Data Model and Migration
**Exit:** core schema migrated and tested; account/transaction/transfer APIs;
Excel import pipeline with dry-run, provenance, reconciliation report and the
six defect investigations resolved or flagged.
Covers: Epics 3, 4 (+ Epic 2 completion).

### M3 — Core Budgeting MVP
**Exit:** usable app — budgets (original/revised/actual/variance), manual
transactions, reconciliation UI, dashboards v1. This is the first point the
app is genuinely useful day-to-day.
Covers: Epic 5, Epic 16 core UI.

### M4 — Payroll and Specialist Modules
**Exit:** payroll engine (versioned UK tax, overtime, payslip reconcile),
household transfer rules, savings/goals/loans incl. migrated family loan,
Christmas module.
Covers: Epics 8, 9, 10, 11.

### M5 — Banking and Home Assistant Integrations
**Exit:** Starling read-only sync with dedup/matching; cash-flow forecasting
+ safe-to-spend live; custom integration sensors/services; energy cost
estimation.
Covers: Epics 6, 7, 12.

### M6 — Financial Intelligence
**Exit:** subscription detection + price alerts; reporting suite (monthly/
annual/YoY, trends, net worth); explainable forecasts; notification engine.
Covers: Epics 13, 14, 15.

### M7 — Production Readiness and v1.0 Release
**Exit:** full test pyramid green incl. backup/restore/upgrade/rollback;
migration acceptance scenario passed — **workbook retired**; release
checklist, docs, v1.0 tag.
Covers: Epic 17 + hardening backlog.

## 2. MVP vs v1.0

- **MVP (M3):** budgets + manual transactions + reconciliation UI. Already
  useful; does not yet replace the workbook.
- **v1.0 (M7):** everything in scope of the epic set, migration validated,
  workbook retired. All epics' P0–P2 items are v1.0 scope; only explicitly
  labelled P3 items may slide past v1.0.

## 3. Priorities

- **P0** — foundation, data safety, minimum viable path (blocks everything).
- **P1** — essential agreed functionality.
- **P2** — advanced capabilities still in v1.0 scope.
- **P3** — genuine future enhancements (post-v1.0 candidates only).

## 4. Critical path (simplified)

```
M1 platform ─► M2 data model ─► M2 import validation ─► M3 budgeting MVP
      │              │                                       │
      └─ auth/RBAC ──┘              M4 payroll/transfers ◄────┘
                     │                                       │
                     └──────────────► M5 Starling + forecast ─┤
                                                              ▼
                                                   M6 intelligence ─► M7 release
```

Import validation precedes switching off Excel; stable internal APIs precede
HA sensors; tested backup/restore precedes v1.0.


## 5. Assurance work added 09/11/2026

Two cross-cutting assurance epics have been added to the backlog:

### Excel-to-app functional parity
Parent: [Epic #351](https://github.com/JoshJames04/family-finance-ha/issues/351)

- [ ] [#353](https://github.com/JoshJames04/family-finance-ha/issues/353)
- [ ] [#354](https://github.com/JoshJames04/family-finance-ha/issues/354)
- [ ] [#355](https://github.com/JoshJames04/family-finance-ha/issues/355)
- [ ] [#356](https://github.com/JoshJames04/family-finance-ha/issues/356)
- [ ] [#357](https://github.com/JoshJames04/family-finance-ha/issues/357)
- [ ] [#358](https://github.com/JoshJames04/family-finance-ha/issues/358)
- [ ] [#359](https://github.com/JoshJames04/family-finance-ha/issues/359)
- [ ] [#360](https://github.com/JoshJames04/family-finance-ha/issues/360)
- [ ] [#361](https://github.com/JoshJames04/family-finance-ha/issues/361)
- [ ] [#362](https://github.com/JoshJames04/family-finance-ha/issues/362)

This work verifies workbook behaviours and historical data through backend, frontend and deterministic tests. Excel retirement remains blocked until issue #362 is accepted and Alice explicitly authorises retirement.

### Engineering robustness, security and operational assurance
Parent: [Epic #352](https://github.com/JoshJames04/family-finance-ha/issues/352)

- [ ] [#363](https://github.com/JoshJames04/family-finance-ha/issues/363)
- [ ] [#364](https://github.com/JoshJames04/family-finance-ha/issues/364)
- [ ] [#365](https://github.com/JoshJames04/family-finance-ha/issues/365)
- [ ] [#366](https://github.com/JoshJames04/family-finance-ha/issues/366)
- [ ] [#367](https://github.com/JoshJames04/family-finance-ha/issues/367)
- [ ] [#368](https://github.com/JoshJames04/family-finance-ha/issues/368)

These checks harden CI/CD, ingress/RBAC, ledger and Starling invariants, backup/restore, migrations/releases, observability and operator documentation. Work is to be delivered via focused PRs with evidence; no production deployment or real-data migration without Alice's explicit approval.

**Project status:** Backlog / Not started. Update issue and project-board status as work begins and PRs merge; do not mark complete based on implementation presence alone.
