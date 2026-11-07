# ADR-0005: Authentication & Authorisation Model

Status: Accepted
Date: 2026-11-06 (validated by spike #27 on 2026-11-07)

## Context

HA ingress gives us an authenticated HA session — but HA knows nothing about
household finance roles, and dashboard visibility is not a security
boundary. Standalone mode has no HA session at all.

## Options considered

- **HA authN + app-level authZ (chosen):** ingress mode trusts the
  supervisor-injected `X-Remote-User-*` headers **only** when requests arrive
  via the supervisor socket; maps HA user → `user_profile` → role. Standalone
  mode uses app-managed sessions (secure cookie) with the same profile/RBAC
  layer.
- Trust HA entirely (no app authZ): fails shared/private data requirements.
- Fully independent auth in all modes: re-authenticating inside HA is bad UX.

## Decision

Two auth paths, one identity/permission layer. RBAC:
`admin` > `member` > `viewer`; data scope `shared | private(owner)` enforced
in repository queries and API dependencies — never UI-filtered.

Spike #27 validated the concrete mechanics
(docs/spikes/0027-ha-auth.md):

- Ingress injects `X-Remote-User-Id` (stable UUID — the profile key),
  `X-Remote-User-Name`, `X-Remote-User-Display-Name` and
  `X-Remote-User-Groups` after stripping client-supplied copies —
  smuggling is impossible *through* ingress, so the verification duty is
  asserting the request arrived from `172.30.32.2` (and ignoring
  remote-user headers on any other socket).
- First-run bootstrap: first auto-provisioned profile becomes `admin`.
- Standalone mode: app-managed sessions (Secure/HttpOnly/SameSite cookie,
  server-side store + rotation); credentials via local password hash, or
  `POST /auth` against the Supervisor when `auth_api` is reachable — the
  same profile/RBAC layer regardless.

## Consequences

- Ingress-header spoofing protection is a P0 security test.
- Every endpoint gains an authz dependency; audit logs record actor + role.

## Validation / revisit triggers

Revisit if HA changes ingress trust semantics or if standalone mode needs
SSO/MFA.
