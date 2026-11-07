"""Role-based access control (issue #28, ADR-0005).

The matrix lives here, in the domain — roles are not merely stored,
they are enforced by callers through ``can``/``assert_can`` and by
query-layer ``visible_to`` helpers. ``None`` profile = anonymous
(pre-auth placeholder semantics): read-only on shared resources.

Scope model: a resource is either shared (household-wide) or
private(owner). Admin > member > viewer:

- admin: everything, plus profile/secret management and every scope
- member: read+write shared, read+write own-private
- viewer: read shared only — no writes anywhere
- anonymous: read shared only (matches the pre-auth placeholder)
"""

from __future__ import annotations

import enum

from app.persistence.models import Role, UserProfile


class Action(enum.StrEnum):
    read = "read"
    write = "write"
    delete = "delete"
    manage_profiles = "manage_profiles"
    manage_secrets = "manage_secrets"
    export = "export"
    admin = "admin"             # catch-all admin surfaces


class Scope(enum.StrEnum):
    shared = "shared"
    own_private = "own_private"
    other_private = "other_private"


class PermissionDenied(PermissionError):
    pass


_MATRIX: dict[Role | None, frozenset[tuple[Action, Scope]]] = {
    Role.admin: frozenset(
        (a, s) for a in Action for s in Scope),
    Role.member: frozenset([
        (Action.read, Scope.shared),
        (Action.write, Scope.shared),
        (Action.export, Scope.shared),
        (Action.read, Scope.own_private),
        (Action.write, Scope.own_private),
        (Action.export, Scope.own_private),
    ]),
    Role.viewer: frozenset([
        (Action.read, Scope.shared),
    ]),
    None: frozenset([
        (Action.read, Scope.shared),
    ]),
}


def scope_of(owner_profile_id: int | None,
             viewer_id: int | None) -> Scope:
    """Classify a resource's scope for a given viewer."""
    if owner_profile_id is None:
        return Scope.shared
    if viewer_id is not None and owner_profile_id == viewer_id:
        return Scope.own_private
    return Scope.other_private


def can(profile: UserProfile | None, action: Action,
        scope: Scope = Scope.shared) -> bool:
    if profile is not None and not profile.is_active:
        return False                     # deactivated: immediate
    role = profile.role if profile is not None else None
    return (action, scope) in _MATRIX[role]


def assert_can(profile: UserProfile | None, action: Action,
               scope: Scope = Scope.shared) -> None:
    if not can(profile, action, scope):
        raise PermissionDenied(
            f"{action.value} on {scope.value} denied")


def visible(owner_profile_id: int | None,
            profile: UserProfile | None) -> bool:
    """Query-layer scope filter (issue #32): private(owner) rows are
    invisible to other members — not hidden, not found."""
    scope = scope_of(owner_profile_id,
                     profile.id if profile is not None else None)
    return can(profile, Action.read, scope)


def write_allowed(owner_profile_id: int | None,
                  profile: UserProfile | None) -> bool:
    """Mutation gate (issue #32). ``None`` profile keeps the pre-auth
    compat posture (open single-user mode); any *resolved* identity
    is fully enforced — a viewer can never mutate."""
    if profile is None:
        return True
    scope = scope_of(owner_profile_id, profile.id)
    return can(profile, Action.write, scope)


def manage_profiles_allowed(
        profile: UserProfile | None,
        active_admin_exists: bool) -> bool:
    """Profile management requires admin — except the bootstrap
    window: with no active admin yet, management is open so the
    first user can promote (issue #28 bootstrap AC)."""
    if not active_admin_exists:
        return True
    return can(profile, Action.manage_profiles)
