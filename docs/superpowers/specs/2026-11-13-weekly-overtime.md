# Weekly overtime sheets — #487

Approved request: group current and historical overtime by Monday–Sunday work week, submit its draft entries together, approve/reject its submitted entries together, and retain individual actions. The user approved issue #487 and requested implementation, thorough tests and local Docker availability.

## Behaviour

- Group by work_date for the selected employment, newest week first, chronological entries inside each week. All historical entries participate automatically, without rewriting stored records.
- Header: ISO week number and ISO week-year, explicit Monday–Sunday dates, total hours/pay, status counts. A mixed week has a breakdown rather than one misleading status.
- Submit week changes draft → submitted. Approve/reject week changes submitted → approved/rejected. Leave all other states unchanged. Disable empty actions and retain individual submit/approve/reject/rework/edit/delete.
- Confirm each weekly operation with the actual eligible entries, count, total hours/pay and week bounds. New drafts after submission require a fresh submit. Stale membership/content/status must cause a refreshable conflict, never silently process unseen changes.
- One backend transaction validates scope and snapshot, changes all intended eligible entries, and writes per-entry audits with week context. Replays cannot create duplicate effects. Existing household/employment permissions apply, including service restrictions.
- Existing work dates, rates, multiplier snapshots, payroll cutoffs and generated payroll snapshots remain unchanged merely by grouping/action. Cross-year/week-53/month/cutoff weeks preserve each entry's existing payroll treatment.
- Mobile and keyboard users can read summaries and operate group/row actions. Keep established components and styling; no new dependencies or schema migration.
- This is in-app submission/approval tracking. No automatic manager messages, external manager accounts or physical sheet export.

## API contract

`POST /api/v1/employments/{emp_id}/overtime-weeks/{week_start}/{action}` where action is submit/approve/reject and week_start is a Monday.

Body: `{ "entries": [{ "id": 123, "revision": "opaque-content-version" }] }` for the complete set of currently eligible entries in that employment/week, not a selectable subset. OvertimeOut exposes an opaque revision derived from content/status, avoiding same-second timestamp ambiguity. All existing individual endpoints remain available.

Return `{ "week_start": "YYYY-MM-DD", "week_end": "YYYY-MM-DD", "action": "submit", "updated_count": 1 }`. Invalid inputs → 422; stale snapshot/membership/replay → 409; permissions follow the existing employment boundary. No partial mutation/audit on failure. Guard date overflow and duplicate IDs.

## Acceptance / proof

Backend: 1/7 populated days and multiple same-day entries; Monday/Sunday boundaries, ISO new year/week 53, mixed states, newly added drafts, stale content/status/membership and repeat requests; cross-household/employment/service/viewer privacy; rollback/audit counts; rates and approved-only payroll across cutoffs unchanged.

Frontend: historical grouping and ordering, UTC/DST-safe date arithmetic, exact totals/status breakdowns, eligible action controls, confirmation/cancel/busy/conflict recovery, retained individual actions, edit moving groups and new drafts after submission. Browser proof uses a disposable real local API and mobile/desktop layouts. Run full backend/frontend suites, lint/typecheck/build, performance checks and required CI; publish a ready bot-authored PR into develop and request JoshJames04 plus native Codex review. Update the local preview only after focused verification; leave live data untouched.
