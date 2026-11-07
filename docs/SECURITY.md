# Security & Data Protection — Family Finance

**Status:** Requirements — implementation tracked under `area:security`
issues (Epic 2 and cross-cutting tasks).

## 1. Threat context

Self-hosted app holding highly sensitive household financial data (salaries,
balances, debts, transactions, bank tokens). Adversaries include: anything on
the LAN, malicious/compromised HA integrations, browser attacks via ingress,
credential theft from backups, and accidental self-exposure (logs, exports,
mobile lock screens).

## 2. Authentication & authorisation

- **AuthN:** trust HA's authenticated session at the ingress layer; in
  standalone mode, app-managed auth. Never trust a header without verifying
  HA actually injected it.
- **AuthZ:** application-level RBAC — `admin` / `member` / `viewer` —
  enforced in the API on every request. HA dashboard visibility is **not**
  a security boundary.
- **Data scoping:** shared household data vs `private(owner)` data scoped in
  queries, not UI hiding.

## 3. Data protection

- Credentials & bank tokens in an encrypted vault (key from HA secrets /
  local keyring), never in tables, options, logs, exports or the frontend.
- TLS terminated by HA/proxy; app never binds a public port.
- Backups encrypted at rest via HA backup protection; app snapshots obey the
  same policy.
- Audit trail (append-only) on all financial mutations and auth events.
- No real financial data in dev, CI, tests, issues or docs — synthetic
  fixtures only (see `tests/fixtures/README.md`).

## 4. API & supply chain

- Input validation on every endpoint (pydantic schemas); parameterised
  queries only.
- CSRF-safe mutation patterns; rate limiting on auth-adjacent endpoints.
- Secret scanning (CI) + dependency vulnerability scanning (Dependabot/CI);
  pinned dependencies with review policy.
- Least-privilege Starling scopes; read-only; no payment initiation.
- `SECURITY.md` at repo root defines the disclosure policy.

### 4.1 Standing verification (CI gates)

Security is verified continuously, not once:

- `backend/tests/test_security.py` runs in the normal suite and asserts
  the current guarantees: `X-Household-Id` header spoofing is rejected
  (unknown/non-integer IDs), cross-household access on every scoped
  route returns 404/409, the audit log has no mutation routes and an
  append-only model (no `updated_at`), and hostile inputs (injection
  strings, oversized payloads, invalid pagination) are rejected or
  stored safely.
- Dependabot covers pip, npm and GitHub Actions ecosystems.
- The `tests` workflow fails on `pip-audit` (OSV) or `npm audit
  --audit-level=high` findings — new critical/high vulnerabilities
  block merges.
- `repo-hygiene` fails on forbidden files (workbooks, secrets, DBs)
  and runs gitleaks.

## 5. Privacy UX

- Notification content suppresses amounts/payees on lock-screen-visible
  surfaces (configurable).
- HA sensors opt-in; sensitive entities disabled by default.

## 6. Non-requirements

- No telemetry, no external analytics, no outbound calls except configured
  integrations (Starling) and update checks (opt-in).

## 7. Migration/import threat notes

- The workbook parser opens files read-only and never writes to the
  source; source hash + mtime are recorded so tampering between runs is
  detectable.
- Imported rows are staged as `import_record` before commit; nothing
  reaches the ledger without an extractor decision.
- Rollback voids derived rows rather than deleting them, preserving the
  audit trail.
- The real workbook and any file derived from it are forbidden in the
  repository (`.gitignore` + CI gate); only synthetic fixtures are used
  in tests.
