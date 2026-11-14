# Release 1.5.0 candidate and remaining acceptance

This is a release-preparation checklist. It does not authorise merging, tagging,
publishing, live deployment or private data/configuration changes.

The previous 1.4.2 patch-version exception applied only to that batch. This batch
adds payday Budget windows and weekly overtime, so the repository's ordinary
backwards-compatible-feature rule selects 1.5.0.

## Implementation included in this candidate

- [x] Forecast/matching correction (#491, #494; PR #493).
- [x] Dashboard preference recovery (#492; PR #495).
- [x] Bank-sync ownership, queue/retry and balance-repair correction (#490).
- [x] Budget receipt reconciliation and owner-labelled All sections (#483, #466).
- [x] Frontend dependency updates with a common Node 22 production API and
      verified Node 24 Compose development; Docker action upgrades.
- [x] Scheduled dependency routing/assignment (#508) and sole-Josh code ownership
      with current-head approval, without activating automatic merging (#510).

The notes candidate contains the remaining dependency commits so its source
matches these descriptions. Merge their individual reviewed PRs first, then
merge the notes PR; this does not authorise skipping their CI or approvals.

## Before promoting develop to main

- [ ] Merge the reviewed dependency PRs #506, #505 and #507 in that order, then
      the final release-notes PR, into develop.
- [ ] Reconcile the changelog and release closing keywords against the actual
      final aggregate diff; do not claim an unmerged fix is included.
- [ ] Full required CI and native Codex review pass on the exact release head.
- [ ] Complete the seven-check machine release gate, production-copy migration
      upgrade/downgrade/upgrade, version consistency and verified backup restore.
- [ ] Review the aggregate release diff and its operational risks.

Fixing PRs use `Closes #N` for satisfied issues. The release PR must carry the
applicable closing keywords because GitHub closes automatically on `main`, not
`develop`. Use references for outstanding acceptance; do not close a whole
parent because one implementation child passed tests.

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
| #360 | Map sixteen recovered source comments to verified app records with private provenance. Do not guess destinations or publish workbook contents. |

The publishing App cannot access the personal Family Finance Project V2 board.
The owner explicitly requires bot-only access; record statuses and PR evidence in
issue comments until the App has supported project access. Do not switch to a
human login to update the board.
