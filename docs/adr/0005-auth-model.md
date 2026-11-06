# ADR-0005: Authentication & Authorisation Model

Status: Proposed
Date: 2026-11-06

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

## Consequences

- Ingress-header spoofing protection is a P0 security test.
- Every endpoint gains an authz dependency; audit logs record actor + role.

## Validation / revisit triggers

Revisit if HA changes ingress trust semantics or if standalone mode needs
SSO/MFA.
