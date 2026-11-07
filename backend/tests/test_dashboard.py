"""Issue #123 — per-member dashboard layout persistence: GET/PUT
roundtrip, role echo for widget gating, standalone no-profile
degradation."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


def _app(tmp_path: Path, **env: str):
    return create_app(Settings.load({"FF_DATA_DIR": str(tmp_path), **env}))


def _seed_profile(client: TestClient, name: str = "Mum",
                  role: str = "member") -> dict:
    hid = client.post("/api/v1/households",
                      json={"name": "H"}).json()["id"]
    return client.post("/api/v1/profiles",
                       json={"display_name": name, "role": role},
                       headers={"X-Household-Id": str(hid)}).json()


class TestDashboardLayout:
    def test_roundtrip(self, tmp_path: Path) -> None:
        with TestClient(_app(tmp_path)) as c:
            p = _seed_profile(c)
            headers = {"X-Profile-Id": str(p["id"])}
            layout = {"order": ["upcoming", "accounts"],
                      "hidden": ["forecast"]}
            r = c.put("/api/v1/profiles/me/dashboard",
                      json=layout, headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["layout"]["order"][0] == "upcoming"
            got = c.get("/api/v1/profiles/me/dashboard",
                        headers=headers)
            assert got.json()["layout"] == layout
            assert got.json()["role"] == "member"

    def test_second_profile_isolated(self, tmp_path: Path) -> None:
        """Layouts are per-member — one member's arrangement must not
        leak into another's (issue #123 AC)."""
        with TestClient(_app(tmp_path)) as c:
            a = _seed_profile(c, "A")
            hid = c.get("/api/v1/households").json()["items"][0]["id"]
            b = c.post("/api/v1/profiles",
                       json={"display_name": "B", "role": "member"},
                       headers={"X-Household-Id": str(hid)}).json()
            c.put("/api/v1/profiles/me/dashboard",
                  json={"order": ["accounts"], "hidden": []},
                  headers={"X-Profile-Id": str(a["id"])})
            got = c.get("/api/v1/profiles/me/dashboard",
                        headers={"X-Profile-Id": str(b["id"])})
            assert got.json()["layout"] is None

    def test_no_profile_degrades(self, tmp_path: Path) -> None:
        """Standalone with no profile → layout null, role null; the
        client falls back to its local default."""
        with TestClient(_app(tmp_path)) as c:
            c.post("/api/v1/households", json={"name": "H"})
            r = c.get("/api/v1/profiles/me/dashboard")
            assert r.status_code == 200
            assert r.json() == {"role": None, "layout": None}
            assert c.put("/api/v1/profiles/me/dashboard",
                         json={"order": [], "hidden": []}
                         ).status_code == 403
