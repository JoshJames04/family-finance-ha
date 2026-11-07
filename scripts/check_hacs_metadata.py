#!/usr/bin/env python3
"""HACS custom-repository installability check (issue #105).

The upstream ``hacs/action`` refuses to run on private repositories,
so this script performs the same structural validation locally:

- ``hacs.json`` at repo root with a ``name`` and only known keys.
- ``custom_components/<domain>/manifest.json`` carrying every key HA
  requires, ``domain`` matching the directory, and a semver
  ``version`` (HA refuses to load custom integrations without one).
- ``config_flow: true`` implies UI strings exist
  (``strings.json`` + ``translations/en.json``).
- ``render_readme: true`` implies a README exists.

    python scripts/check_hacs_metadata.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = ROOT / "custom_components"

SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?")

MANIFEST_REQUIRED = {
    "domain",
    "name",
    "codeowners",
    "documentation",
    "integration_type",
    "iot_class",
    "version",
}
INTEGRATION_TYPES = {
    "hub", "device", "service", "helper", "entity", "hardware",
    "system",
}
HACS_KNOWN_KEYS = {
    "name", "homeassistant", "hacs", "render_readme", "content_in_root",
    "zip_release", "filename", "country", "hide_default_branch",
}

errors: list[str] = []


def _fail(msg: str) -> None:
    errors.append(msg)
    print(f"FAIL {msg}", file=sys.stderr)


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(f"missing {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        _fail(f"{path.relative_to(ROOT)} is not valid JSON: {exc}")
    return {}


def check_hacs_json() -> None:
    data = _load(ROOT / "hacs.json")
    if not data:
        return
    if not data.get("name"):
        _fail("hacs.json: 'name' is required")
    unknown = set(data) - HACS_KNOWN_KEYS
    if unknown:
        _fail(f"hacs.json: unknown keys {sorted(unknown)}")
    if data.get("render_readme") and not (ROOT / "README.md").exists():
        _fail("hacs.json render_readme=true but no README.md")


def check_integrations() -> int:
    if not COMPONENTS.is_dir():
        print("no custom_components/ — nothing to validate")
        return 0
    found = False
    for domain_dir in sorted(p for p in COMPONENTS.iterdir()
                             if p.is_dir()):
        manifest_path = domain_dir / "manifest.json"
        if not manifest_path.exists():
            continue  # not an integration directory
        found = True
        manifest = _load(manifest_path)
        if not manifest:
            continue
        rel = manifest_path.relative_to(ROOT)
        missing = MANIFEST_REQUIRED - set(manifest)
        if missing:
            _fail(f"{rel}: missing keys {sorted(missing)}")
        domain = manifest.get("domain")
        if domain and domain != domain_dir.name:
            _fail(f"{rel}: domain '{domain}' != directory "
                  f"'{domain_dir.name}'")
        itype = manifest.get("integration_type")
        if itype and itype not in INTEGRATION_TYPES:
            _fail(f"{rel}: bad integration_type '{itype}'")
        version = manifest.get("version")
        if version and not SEMVER.fullmatch(str(version)):
            _fail(f"{rel}: version '{version}' is not semver")
        if manifest.get("config_flow"):
            for fname in ("strings.json",
                          "translations/en.json"):
                if not (domain_dir / fname).exists():
                    _fail(f"{rel}: config_flow=true but no {fname}")
        if not (domain_dir / "__init__.py").exists():
            _fail(f"{rel}: no __init__.py")
    if not found:
        print("no integration manifests — nothing to validate")
    return 0


if __name__ == "__main__":
    check_hacs_json()
    check_integrations()
    if errors:
        sys.exit(1)
    print("HACS metadata OK")
