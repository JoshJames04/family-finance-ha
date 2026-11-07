# Spike #27: Home Assistant authentication integration

Status: complete (docs + Supervisor source verified; no live dev
instance was available for the experimental header check — that
verification is a checklist item in #29's test plan)
Date: 2026-11-07
Sources: Supervisor `api/ingress.py` (`_REQUEST_HEADERS_FILTER`,
`_init_header`), developers.home-assistant.io add-on security +
ingress guides, supervisor PR #4152.

## Findings

### What ingress sends

When a request reaches the addon via ingress, the Supervisor
injects identity headers after **stripping any client-supplied
copies** (they're in `_REQUEST_HEADERS_FILTER`, removed before the
trusted values are set — case-insensitively, so smuggling via
`x-remote-user-id`/`X-REMOTE-USER-ID` variants is impossible):

- `X-Remote-User-Id` — stable HA user UUID (the mapping key)
- `X-Remote-User-Name` — login username
- `X-Remote-User-Display-Name` — display name
- `X-Remote-User-Groups` — comma-separated HA group ids
- `X-Ingress-Path` — the install-specific mount prefix (needed for
  any absolute URL building; the frontend must stay relative)

### The trust boundary is network-level, not crypto

Headers are trustworthy **iff** the request genuinely came through
the Supervisor. The Supervisor proxies from `172.30.32.2` — the
documented hard requirement is that the app:

- listens only on the ingress port,
- accepts connections only from `172.30.32.2` (deny everything
  else — a direct-container port would see unfiltered headers).

So: *header verification = source-IP verification*. If the addon
ever binds externally, all `X-Remote-User-*` headers are attacker-
controlled and must be ignored outside ingress mode.

### Fallback credential check without ingress

`auth_api: true` in `config.yaml` exposes `POST http://supervisor/auth`
— username+password validated against the HA auth backend (401 on
failure). This gives standalone mode a path that never stores HA
credentials: our own session cookie after a successful `/auth` call.

## Design (feeds ADR-0005 + issue #29)

**Ingress mode** (`FF_ENV=prod`, addon):

1. Middleware asserts `request.client.host == "172.30.32.2"` → else
   403 (defence in depth: also drop `X-Remote-User-*` from any
   non-supervisor source rather than trusting absence).
2. `X-Remote-User-Id` → `user_profile.external_id`; username +
   display-name refresh on every request (cheap, keeps profiles
   current); `X-Remote-User-Groups` available for future admin
   mapping.
3. First-run bootstrap: first profile created gets `admin` role;
   subsequent HA users default to `member` until an admin promotes.

**Standalone mode** (dev / non-addon install):

- App-managed session: `POST /auth/login` → `Set-Cookie` (Secure,
  HttpOnly, SameSite=Strict, signed session id stored server-side
  with expiry + rotation on privilege change).
- Credential source: local `user_profile` password hash first;
  optional `POST /auth` against the Supervisor when `auth_api` is
  reachable — same profile/RBAC layer either way.

**RBAC** stays app-side regardless: `user_profile.role`
(admin/member) gates admin routes via `require_admin`; household
scoping continues via the existing dep chain.

## Threat notes (for docs/SECURITY.md)

- Ingress trust is IP-based — binding the app beyond the supervisor
  network silently defeats auth. Startup should log/fail when
  ingress headers appear on a non-supervisor socket.
- `X-Remote-User-Id` is a UUID — opaque, non-sequential, safe to
  store as the profile key.
- Session cookies in standalone mode need rotation on role change
  and absolute expiry; logout must invalidate server-side, not just
  drop the cookie.
- `auth_api` caching by the Supervisor is minutes-scale — revoked
  HA passwords can linger briefly; acceptable for the finance app.

## Open questions resolved for implementation

- *Which header is the identity key?* `X-Remote-User-Id` (UUID —
  usernames/display names are mutable decoration).
- *How does a non-owner member get a profile?* Auto-provisioned on
  first ingress request (member role); admin promotes as needed.
- *Experimental header check?* Deferred to #29 — dev addon with a
  header-echo endpoint asserts smuggled headers are stripped.
