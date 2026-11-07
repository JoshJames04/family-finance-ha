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

## Verification before claiming done

- `cd backend && python -m pytest tests/ -q` (full suite)
- `python -m ruff check app/ tests/` and `python -m mypy app/`
- New migration → `alembic upgrade head`, then downgrade one step,
  upgrade again; `alembic heads` must print exactly one head.
- `cd frontend && npm run typecheck && npm test && npm run build`

## Data safety

- Never commit `Family Budget.xlsx`, real financial data, tokens,
  or account numbers — the repo is public.
- Credentials go through the vault (`domain/vault.py`) as opaque
  refs; bank tokens never persist on connection rows.
