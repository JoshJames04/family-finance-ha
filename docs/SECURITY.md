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
- **Ingress trust boundary (spike #27):** `X-Remote-User-*` headers are
  trustworthy only because the Supervisor strips client copies and
  re-injects its own. Header trust therefore reduces to *source trust* —
  the backend accepts them only from `172.30.32.2`, and any deployment
  exposing the app outside the supervisor network must disable
  header-based auth entirely. The middleware treats remote-user headers
  on a non-supervisor socket as spoof attempts, not missing auth.
- **Identity key:** `X-Remote-User-Id` (UUID) is the stable profile key;
  username/display-name are refreshed decoration, never lookup keys.
  Under ingress `X-Profile-Id` is ignored server-side (#341) and the UI
  presents no member switcher — identity comes only from the HA user.
  Standalone/dev mode keeps `X-Profile-Id` as the explicit non-ingress
  path.
- **Standalone sessions:** Secure + HttpOnly + SameSite=Strict cookies,
  server-side session store, rotation on privilege change, absolute
  expiry; logout invalidates server-side.
- **AuthZ:** application-level RBAC — `admin` / `member` / `viewer` —
  enforced in the API on every request. HA dashboard visibility is **not**
  a security boundary.
- **Data scoping:** shared household data vs `private(owner)` data scoped in
  queries, not UI hiding.

### 2.1 Ingress trust audit (#364, 2026-11-09)

Verified on `develop` — `TestIngressTrust` + `TestIngressTrustMatrix` in
`backend/tests/test_auth.py`:

- Remote-user headers from **any** non-`172.30.32.2` peer → 401 before
  routing, in **both** ingress and standalone modes — including partial
  header sets (uid absent) and requests that also carry a valid session.
- The trust decision reads the transport peer (`request.client`), never
  `X-Forwarded-For` — an XFF claim of the supervisor IP cannot launder an
  untrusted peer. Uvicorn is not run with `--proxy-headers` trusts beyond
  its localhost-only default.
- Under ingress: `X-Profile-Id` resolves nobody, headerless requests are
  anonymous, and the first supervisor identity bootstraps household+admin
  — subsequent identities provision as `member`.
- Add-on publishes no host port by default (`8099/tcp: null`); if an
  operator publishes it, the supervisor-IP allowlist remains the barrier
  and standalone session auth still applies.
- Household scoping + deny-by-default route coverage:
  `test_cross_scope_on_every_scoped_route`,
  `test_every_route_has_authz_or_is_public`.

## 3. Data protection

- Credentials & bank tokens in an encrypted vault (key from HA secrets /
  local keyring), never in tables, options, logs, exports or the frontend.
- TLS terminated by HA/proxy; app never binds a public port.
- Backups encrypted at rest via HA backup protection; app snapshots obey the
  same policy.
- Audit trail (append-only) on all financial mutations and auth events:
  SQLite triggers `audit_event_no_update`/`audit_event_no_delete` abort
  UPDATE/DELETE at the database layer, and amount-like payload keys are
  stored as sha256 digests so the log never carries raw figures.
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
- `backend/tests/test_audit_integrity.py` verifies the append-only
  triggers reject UPDATE/DELETE at the DB layer, statically walks every
  `/api/v1` route to prove each mutation handler emits an audit event,
  and asserts amount-like payload fields are sha256 digests.
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

## 7. Import threat notes

- Statement uploads are size-capped and parsed in memory; each import is
  staged as a `statement_import` preview — nothing reaches the ledger
  until a member commits the reviewed rows.
- Rollback voids derived rows rather than deleting them, preserving the
  audit trail.
- Source financial files (workbooks, exports) are forbidden in the
  repository (`.gitignore` + CI gate); only synthetic fixtures are used
  in tests.

### Forecast and budget scope

Expected movement lists, contribution status, outstanding obligations and
per-account requirements enforce the viewer's account visibility. Accountless
household plans remain visible; legacy salary rows without a destination are
withheld because their account permission boundary is unknown. Selecting a
personal budget also restricts forecast rows and actuals (including splits) to
that budget owner's accounts. Shared budget actuals and bill totals retain their
household accounting meaning; individual forecast rows additionally respect
viewer permissions and can therefore omit another member's private account.
Runtime diagnostics record a fixed scheduler error code rather than exception
messages, which can contain financial or personal details.

Household-wide forecast rematerialisation requires an administrator when a
profile is resolved. Member auto-matching is limited to writable visible
accounts; viewers cannot create even accountless plans or run matching. Legacy
standalone anonymous write compatibility follows the application's existing
RBAC contract.
