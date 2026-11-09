"""Issue #25 — single version source propagated everywhere."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import __version__
from app.core.settings import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]


def _json_version(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8"))["version"])


def test_api_reports_source_version(tmp_path: Path) -> None:
    with TestClient(create_app(
            Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
        assert c.get("/api/v1/version").json()["version"] == __version__


def test_all_targets_match_source() -> None:
    pyproject = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"',
        (ROOT / "backend" / "pyproject.toml").read_text("utf-8"))
    addon = re.search(
        r'(?m)^version\s*:\s*"?([^"\n]+)"?',
        (ROOT / "addon" / "config.yaml").read_text("utf-8"))
    assert pyproject and pyproject.group(1) == __version__
    assert addon and addon.group(1) == __version__
    assert _json_version(
        ROOT / "frontend" / "package.json") == __version__
    assert _json_version(
        ROOT / "custom_components" / "family_finance"
        / "manifest.json") == __version__
