# Working agreements

## Project board hygiene (required every slice)

The GitHub Project "Family Finance" (project 1) is the status source
of truth. Update it **as part of finishing each slice**, not in
batches:

- PR opened → issue(s) move to **In Review**; comment the PR link
  on the issue.
- PR merged → issue moves to **Done** and is closed (the PR body
  uses `Closes #N` where possible).
- Work started, no PR yet → **In Progress**.
- Genuinely cannot proceed → **Blocked** with a comment naming the
  blocker.

Status field ids (project `PVT_kwHOBEl-wc4BqjEk`, field
`PVTSSF_lAHOBEl-wc4BqjEkzho5wkM`): Backlog `7b719211`, Ready
`dfff8830`, In Progress `6515cfba`, In Review `b506291e`, Blocked
`a113bf8c`, Done `ee145375`. Epic status tracks children: all
children done → epic Done; any child active → In Progress.

## Branch/stack conventions

- `develop` is the integration branch; `main` is release-only.
- Issue slices are stacked `feature/<issue>-<slug>` branches; each
  PR bases on the previous slice's branch, not develop, unless it
  is the bottom of the stack.
- CI (`tests.yml` + `repo-hygiene`) runs on every PR — the repo is
  public, so runs are free; do not skip fixing red CI.

## Documentation currency (required every change)

README.md and docs/ are living documents — every PR that changes
behaviour, security, ops, deployment or workflows must update the
affected docs **in the same PR**, not a follow-up. When unsure which
doc owns a fact, check: README (feature overview + getting started),
docs/SECURITY.md (identity/authZ), docs/MIGRATION.md (moving installs,
workbook retirement), docs/CICD-HA.md (pipeline + required checks),
docs/ARCHITECTURE.md (design), AGENTS.md (verification + workflow).

## Verification before claiming done

- `cd backend && python -m pytest tests/ -q -m "not perf"` (full suite)
- `python -m pytest -m perf` runs the non-gating perf budgets (#132)
- `python -m ruff check app/ tests/` and `python -m mypy app/`
- New migration → `alembic upgrade head`, then downgrade one step,
  upgrade again; `alembic heads` must print exactly one head.
- `cd frontend && npm run typecheck && npm test && npm run build`

## Data safety

- Never commit `Family Budget.xlsx`, real financial data, tokens,
  or account numbers — the repo is public.
- Credentials go through the vault (`domain/vault.py`) as opaque
  refs; bank tokens never persist on connection rows.


## PR review & merge workflow (post-2026-11-07)

- `main` is the repo's default branch; `develop` remains the integration branch. All feature/fix PRs target `develop` directly; releases flow `develop → main`.
- GitHub only fires `Closes #N` on merges to the default branch (`main`), so develop merges no longer auto-close issues — close the issue manually when its PR merges (or let the release PR close it at ship time). Keep `Closes #<issue>` in PR bodies for traceability.
- Publish through the approved owner-specific GitHub App and request `JoshJames04` as reviewer. Enabling auto-merge is not a formal approving review. Under the approved sole-maintainer exception, required approval count is zero and code-owner approval is not mandatory. Native Codex remains supplemental review.
- `develop` and `main` are protected: keep all check contexts required (`backend`, `frontend`, `migrations`, `e2e`, `image`, `hacs-validate`, `ha-integration` matrix, `docs-integrity`, `forbidden-files`, `secret-scan`, `version-consistency`); zero required approvals under the approved sole-maintainer exception. Require PRs, resolved conversations and administrator enforcement; publishing bots have no bypass. Keep force-push and deletion blocks. Before enabling independent owner approval, add a trusted write collaborator to the applicable CODEOWNERS entries.
- Merge order: bottom-up (#225 first). Merge commits only — squash rewrites SHAs and pollutes stacked diffs. Each merge shrinks the next PR's diff automatically.
- Assign `JoshJames04` on every PR (`--add-assignee`); reviewer requests are ignored for self-authored PRs.
