"""Issue #29 — authn/authz middleware: spoof defence, ingress
resolution, standalone sessions, deny-by-default coverage."""

from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.api.auth as auth
from app.api import deps
from app.core import security
from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import UserProfile


@pytest.fixture(autouse=True)
def _clear_throttle():
    """The login limiter is process-global — other suites (the
    contract fuzzer) can exhaust it. Each test starts clean."""
    auth._attempts.clear()
    yield
    auth._attempts.clear()


def _app(tmp_path: Path, **env: str):
    return create_app(Settings.load(
        {"FF_DATA_DIR": str(tmp_path), **env}))


def _seed_profile(client: TestClient, name: str = "Mum",
                  role: str = "admin",
                  password: str | None = None) -> dict:
    """Create a household + profile through the public bootstrap
    surface; optionally set a local password for standalone login."""
    hid = client.post("/api/v1/households",
                      json={"name": "H"}).json()["id"]
    p = client.post("/api/v1/profiles",
                    json={"display_name": name, "role": role},
                    headers={"X-Household-Id": str(hid)}).json()
    if password is not None:
        with Session(client.app.state.engine) as s:
            row = s.get(UserProfile, p["id"])
            row.password_hash = security.hash_password(password)
            s.commit()
    return p


class TestIngressTrust:
    def test_spoofed_remote_headers_denied(self, tmp_path: Path):
        """AC: remote-user headers from a non-supervisor source
        must → 401 before routing."""
        with TestClient(_app(tmp_path)) as c:
            c.post("/api/v1/households", json={"name": "H"})
            r = c.get("/api/v1/auth/me", headers={
                "X-Remote-User-Id": "evil",
                "X-Remote-User-Name": "mallory"})
            assert r.status_code == 401

    def test_supervisor_headers_resolve(self, tmp_path: Path,
                                        monkeypatch):
        monkeypatch.setattr(auth, "SUPERVISOR_IP", "testclient")
        with TestClient(_app(tmp_path, FF_INGRESS="true")) as c:
            c.post("/api/v1/households", json={"name": "H"})
            r = c.get("/api/v1/auth/me", headers={
                "X-Remote-User-Id": "ha-123",
                "X-Remote-User-Display-Name": "Dad"})
            assert r.status_code == 200
            # First HA identity in the household bootstraps admin.
            assert r.json()["role"] == "admin"
            assert r.json()["display_name"] == "Dad"

    def test_second_ingress_user_is_member(self, tmp_path: Path,
                                           monkeypatch):
        monkeypatch.setattr(auth, "SUPERVISOR_IP", "testclient")
        with TestClient(_app(tmp_path, FF_INGRESS="true")) as c:
            c.post("/api/v1/households", json={"name": "H"})
            c.get("/api/v1/auth/me", headers={
                "X-Remote-User-Id": "ha-1"})
            r = c.get("/api/v1/auth/me", headers={
                "X-Remote-User-Id": "ha-2",
                "X-Remote-User-Name": "Kid"})
            assert r.json()["role"] == "member"

    def test_profile_header_ignored_under_ingress(
            self, tmp_path: Path, monkeypatch):
        """Spoof hole: X-Profile-Id must not resolve under ingress —
        identity may only come from the supervisor."""
        monkeypatch.setattr(auth, "SUPERVISOR_IP", "testclient")
        with TestClient(_app(tmp_path, FF_INGRESS="true")) as c:
            p = _seed_profile(c)
            r = c.get("/api/v1/auth/me",
                      headers={"X-Profile-Id": str(p["id"])})
            assert r.status_code == 401


class TestStandaloneSessions:
    ENV = {"FF_ENV": "ci"}
    def test_login_cookie_and_me(self, tmp_path: Path):
        with TestClient(_app(tmp_path, **self.ENV)) as c:
            _seed_profile(c, password="hunter2-test-pw")
            r = c.post("/api/v1/auth/login",
                       json={"username": "Mum",
                             "password": "hunter2-test-pw"})
            assert r.status_code == 200
            cookie = r.headers["set-cookie"]
            assert "httponly" in cookie.lower()
            assert "samesite=strict" in cookie.lower()
            assert security.SESSION_COOKIE in cookie
            me = c.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["display_name"] == "Mum"

    def test_bad_password_denied(self, tmp_path: Path):
        with TestClient(_app(tmp_path, **self.ENV)) as c:
            _seed_profile(c, password="right")
            r = c.post("/api/v1/auth/login",
                       json={"username": "Mum", "password": "wrong"})
            assert r.status_code == 401

    def test_anonymous_denied(self, tmp_path: Path):
        with TestClient(_app(tmp_path, **self.ENV)) as c:
            _seed_profile(c)
            assert c.get("/api/v1/auth/me").status_code == 401

    def test_role_change_kills_session(self, tmp_path: Path):
        """AC: session invalidates on role change. A second client
        acts as the admin — an authenticated session wins over the
        compat header, so the same client can't switch identity."""
        app = _app(tmp_path, **self.ENV)
        with TestClient(app) as c, TestClient(app) as admin_c:
            p = _seed_profile(c, password="pw")
            dad = admin_c.post("/api/v1/profiles", json={
                "display_name": "Dad", "role": "admin"}).json()
            assert c.post("/api/v1/auth/login", json={
                "username": "Mum", "password": "pw"}).status_code == 200
            assert c.get("/api/v1/auth/me").status_code == 200
            r = admin_c.patch(f"/api/v1/profiles/{p['id']}",
                              json={"role": "viewer"},
                              headers={"X-Profile-Id": str(dad["id"])})
            assert r.status_code == 200
            assert c.get("/api/v1/auth/me").status_code == 401

    def test_deactivation_kills_session(self, tmp_path: Path):
        """AC: session invalidates on deactivation."""
        app = _app(tmp_path, **self.ENV)
        with TestClient(app) as c, TestClient(app) as admin_c:
            admin = _seed_profile(admin_c, name="Boss")
            p = admin_c.post("/api/v1/profiles", json={
                "display_name": "Kid", "role": "member"}).json()
            with Session(c.app.state.engine) as s:
                s.get(UserProfile, p["id"]).password_hash = (
                    security.hash_password("pw"))
                s.commit()
            assert c.post("/api/v1/auth/login", json={
                "username": "Kid", "password": "pw"}).status_code == 200
            assert c.get("/api/v1/auth/me").status_code == 200
            r = admin_c.post(
                f"/api/v1/profiles/{p['id']}/deactivate",
                headers={"X-Profile-Id": str(admin["id"])})
            assert r.status_code == 200
            assert c.get("/api/v1/auth/me").status_code == 401

    def test_logout_revokes(self, tmp_path: Path):
        with TestClient(_app(tmp_path, **self.ENV)) as c:
            _seed_profile(c, password="pw")
            c.post("/api/v1/auth/login",
                   json={"username": "Mum", "password": "pw"})
            assert c.get("/api/v1/auth/me").status_code == 200
            c.post("/api/v1/auth/logout")
            assert c.get("/api/v1/auth/me").status_code == 401

    def test_login_throttled(self, tmp_path: Path):
        auth._attempts.clear()
        try:
            with TestClient(_app(tmp_path)) as c:
                _seed_profile(c, password="pw")
                for _ in range(5):
                    c.post("/api/v1/auth/login", json={
                        "username": "Mum", "password": "nope"})
                r = c.post("/api/v1/auth/login", json={
                    "username": "Mum", "password": "nope"})
                assert r.status_code == 429
        finally:
            auth._attempts.clear()


class TestRouteCoverage:
    """AC: every endpoint has authz coverage — a route lacking an
    auth dependency must be in the explicit public allowlist."""

    PUBLIC = {
        ("POST", "/api/v1/households"),   # bootstrap creates household
        ("POST", "/api/v1/profiles"),     # bootstrap admin window
        ("GET", "/api/v1/version"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/healthz"),              # supervisor/k8s probes
        ("GET", "/readyz"),
        ("GET", "/{full_path:path}"),     # issue #23: public SPA shell
    }
    AUTH_DEPS = {deps.resolve_profile, auth.current_profile,
                 auth.require_profile, deps.require_admin}

    def _deps_of(self, route: APIRoute) -> set:
        found: set = set()
        stack = [route.dependant]
        while stack:
            d = stack.pop()
            for sub in d.dependencies:
                fn = sub.call
                if (fn in self.AUTH_DEPS
                        or getattr(fn, "_ff_auth", False)):
                    found.add(fn)
                stack.append(sub)
        return found

    def test_every_route_has_authz_or_is_public(
            self, tmp_path: Path):
        app = _app(tmp_path)
        uncovered = []
        for r in app.routes:
            if not isinstance(r, APIRoute):
                continue
            for method in r.methods or ():
                if (method, r.path) in self.PUBLIC:
                    continue
                if not self._deps_of(r):
                    uncovered.append(f"{method} {r.path}")
        assert uncovered == []
