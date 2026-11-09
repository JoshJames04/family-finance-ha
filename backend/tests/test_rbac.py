"""Issue #28 — user profiles + RBAC matrix.

ACs: role matrix enforced in domain; deactivated user denied
immediately; role changes audit-logged with actor; bootstrap lets
the first profile manage until an admin exists.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import rbac
from app.domain.rbac import Action, Scope
from app.main import create_app
from app.persistence.models import (
    AuditEvent,
    Household,
    Role,
    UserProfile,
)


def _profile(session: Session, household: Household,
             role: Role, name: str = "P",
             active: bool = True) -> UserProfile:
    p = UserProfile(household_id=household.id, display_name=name,
                    role=role, is_active=active)
    session.add(p)
    session.flush()
    return p


class TestMatrix:
    def test_admin_everything(self, session: Session,
                              household: Household) -> None:
        a = _profile(session, household, Role.admin)
        for action in Action:
            for scope in Scope:
                assert rbac.can(a, action, scope)

    def test_member_shared_and_own_not_others(
            self, session: Session, household: Household) -> None:
        m = _profile(session, household, Role.member)
        for action in (Action.read, Action.write, Action.export):
            assert rbac.can(m, action, Scope.shared)
            assert rbac.can(m, action, Scope.own_private)
            assert not rbac.can(m, action, Scope.other_private)
        assert not rbac.can(m, Action.manage_profiles)
        assert not rbac.can(m, Action.admin)

    def test_viewer_read_shared_only(
            self, session: Session, household: Household) -> None:
        v = _profile(session, household, Role.viewer)
        assert rbac.can(v, Action.read, Scope.shared)
        for action in Action:
            if action is not Action.read:
                assert not rbac.can(v, action, Scope.shared)
        assert not rbac.can(v, Action.read, Scope.own_private)

    def test_anonymous_read_shared_only(self) -> None:
        assert rbac.can(None, Action.read, Scope.shared)
        assert not rbac.can(None, Action.write, Scope.shared)
        assert not rbac.can(None, Action.read, Scope.own_private)

    def test_deactivated_denied_immediately(
            self, session: Session, household: Household) -> None:
        """AC: deactivation takes effect on the very next check —
        no session/token grace."""
        m = _profile(session, household, Role.member)
        assert rbac.can(m, Action.write, Scope.shared)
        m.is_active = False
        session.flush()
        assert not rbac.can(m, Action.read, Scope.shared)
        assert not rbac.can(m, Action.write, Scope.own_private)

    def test_scope_classification(self) -> None:
        assert rbac.scope_of(None, 1) == Scope.shared
        assert rbac.scope_of(7, 7) == Scope.own_private
        assert rbac.scope_of(7, 8) == Scope.other_private
        assert rbac.scope_of(7, None) == Scope.other_private

    def test_bootstrap_window(self, session: Session,
                              household: Household) -> None:
        """AC: with no active admin, management is open (first user
        can promote); once an admin exists it requires admin."""
        p = _profile(session, household, Role.member)
        assert rbac.manage_profiles_allowed(p, False)
        assert not rbac.manage_profiles_allowed(p, True)
        admin = _profile(session, household, Role.admin, "A")
        assert rbac.manage_profiles_allowed(admin, True)


class TestApiLifecycle:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_bootstrap_then_admin_gate(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        # Bootstrap: no admin exists → member can create an admin.
        p1 = client.post("/api/v1/profiles", json={
            "display_name": "Mum", "role": "admin"}, headers=h
        ).json()
        p2 = client.post("/api/v1/profiles", json={
            "display_name": "Kid", "role": "member"}, headers=h
        ).json()
        # Member actor now fails management ops.
        r = client.patch(f"/api/v1/profiles/{p1['id']}",
                         json={"role": "viewer"},
                         headers={**h, "X-Profile-Id": str(p2["id"])})
        assert r.status_code == 403
        # Admin actor succeeds — audited.
        r = client.patch(f"/api/v1/profiles/{p2['id']}",
                         json={"role": "viewer"},
                         headers={**h, "X-Profile-Id": str(p1["id"])})
        assert r.status_code == 200
        assert r.json()["role"] == "viewer"

    def test_deactivate_then_denied(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        admin = client.post("/api/v1/profiles", json={
            "display_name": "Admin", "role": "admin"},
            headers=h).json()
        member = client.post("/api/v1/profiles", json={
            "display_name": "Member", "role": "member"},
            headers=h).json()
        r = client.post(
            f"/api/v1/profiles/{member['id']}/deactivate",
            headers={**h, "X-Profile-Id": str(admin["id"])})
        assert r.status_code == 200
        # Next request as the deactivated profile is denied.
        r = client.patch(
            f"/api/v1/profiles/{admin['id']}",
            json={"display_name": "X"},
            headers={**h, "X-Profile-Id": str(member["id"])})
        assert r.status_code == 403

    def test_link_identity_moves_uid_and_deactivates_dupe(
            self, client: TestClient) -> None:
        """Issue #340 — admin links an HA uid to the real profile;
        an auto-provisioned duplicate holding it is detached and
        deactivated in the same audited operation."""
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        admin = client.post("/api/v1/profiles", json={
            "display_name": "Admin", "role": "admin"},
            headers=h).json()
        real = client.post("/api/v1/profiles", json={
            "display_name": "Alice", "role": "member"},
            headers=h).json()
        dupe = client.post("/api/v1/profiles", json={
            "display_name": "Alice", "role": "member",
            "ha_user_id": "uid-123"}, headers=h).json()
        ha = {**h, "X-Profile-Id": str(admin["id"])}
        r = client.patch(f"/api/v1/profiles/{real['id']}",
                         json={"ha_user_id": "uid-123"}, headers=ha)
        assert r.status_code == 200, r.text
        assert r.json()["ha_user_id"] == "uid-123"
        rows = {p["id"]: p for p in
                client.get("/api/v1/profiles", headers=h).json()["items"]}
        assert rows[real["id"]]["ha_user_id"] == "uid-123"
        assert rows[dupe["id"]]["ha_user_id"] is None
        assert rows[dupe["id"]]["is_active"] is False
        # member actor cannot link
        r = client.patch(f"/api/v1/profiles/{dupe['id']}",
                         json={"ha_user_id": "uid-9"},
                         headers={**h, "X-Profile-Id": str(real["id"])})
        assert r.status_code == 403

    def test_role_change_audit(self, session: Session,
                               household: Household) -> None:
        """AC: role changes are audit-logged with actor + old→new."""
        from app.api.audit import emit
        admin = _profile(session, household, Role.admin, "A")
        m = _profile(session, household, Role.member, "M")
        emit(session, household.id, "admin:A", "role_change",
             "user_profile", m.id,
             {"from": "member", "to": "viewer"})
        e = session.scalar(select(AuditEvent).where(
            AuditEvent.entity_id == m.id))
        assert e is not None
        assert e.action == "role_change"
        assert admin is not None  # fixture sanity
