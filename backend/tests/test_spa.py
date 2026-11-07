"""Issue #23 — SPA serving under the add-on image.

The bundle mounts as a last-registered catch-all: real files and
index.html serve, /api/* routes are untouched, and deep client
links fall back to the shell for ingress-relative routing.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html>spa shell</html>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    with TestClient(create_app(Settings.load(
            {"FF_DATA_DIR": str(tmp_path / "d"), "FF_ENV": "ci",
             "FF_WEB_DIR": str(web)}))) as c:
        yield c


def test_index_at_root(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "spa shell" in r.text


def test_asset_file_served(client: TestClient) -> None:
    r = client.get("/assets/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_deep_link_falls_back(client: TestClient) -> None:
    r = client.get("/transactions/123/edit")
    assert r.status_code == 200
    assert "spa shell" in r.text


def test_api_routes_untouched(client: TestClient) -> None:
    assert client.get("/api/v1/version").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_traversal_rejected(client: TestClient) -> None:
    """..%2f style escapes must never leave the web root."""
    r = client.get("/..%2f..%2fetc%2fpasswd")
    assert r.status_code == 200
    assert "spa shell" in r.text          # not a file → index
    assert "root:" not in r.text
