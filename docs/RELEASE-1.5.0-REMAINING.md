# Release 1.5.0: remaining work and acceptance

Reviewed 15 November 2026. [v1.5.0 is published](https://github.com/JoshJames04/family-finance-ha/releases/tag/v1.5.0)
and release PR #499 is merged. The former candidate merge order is historical;
do not merge superseded dependency or release-preparation PRs.

Release-closing issues #466, #478, #479, #481, #482, #483, #484, #485,
#487, #490, #491, #492, #494, #508 and #510 are closed. A release or passing CI
alone does not establish the installed Home Assistant acceptance below.

The parity parent #351 remains open while child #360 is incomplete. The five
issues awaiting installed-release acceptance are #414, #416, #460, #461 and #477.
Use the [installed-release checklist](LIVE-ACCEPTANCE.md) to record results.

## Required operational acceptance that stays open

| Issue | Evidence still needed |
|---|---|
| #416 | Actual unattended bank transaction-and-balance refresh, restart, backoff and identifiable reconciliation. Keep copied local provider credentials unscheduled. |
| #475 | Apply only confirmed private categorisation configuration through the authorised live administrator workflow, after verified backup and local proof; verify newly synced transactions. |
| #461 | Actual released HA payroll/source/forecast acceptance; distinguish expected salary from received income. |
| #460 | Actual reported HA ingress/API workload recovery with safe request IDs and correlated logs; no false empty values. |
| #414 | Saved non-default layout through routes, refresh and fresh HA login, including failed-save recovery. |
| #477 | Supported physical mobile-device layout/overflow acceptance. |
| #432, #441 | Broader requested automation targeting/family work plus actual visual-editor screenshots, multi-target filtering, trigger variables and live delivery. Synthetic tests are separate evidence. |
| #360 | Installed-release acceptance is blocked on supported historical-note storage (PR #538), followed by private source verification and preservation with provenance. BudgetLine has no annotation field. Do not guess destinations or publish workbook contents. |

Keep the original personal Family Finance Project V2 board current alongside
issue and PR evidence. The publishing App cannot manage this personal board;
the owner has explicitly authorised their own GitHub login for its administration.
Keep repository publishing and PR administration on Orbit Codex Bot. This project
exception does not permit silent human-account fallback for other operations.
