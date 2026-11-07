#!/usr/bin/env python3
"""Single-source version tool (issue #25).

Source of truth: ``backend/app/__init__.py`` ``__version__``.

    python scripts/version.py get      # print current version
    python scripts/version.py check    # fail if any target drifts (CI)
    python scripts/version.py sync     # rewrite all targets to match
    python scripts/version.py set X.Y.Z

Targets kept in lockstep: backend pyproject, frontend package.json,
HA integration manifest, add-on config.yaml. The image gets its
``org.opencontainers.image.version`` label from this value at build.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "backend" / "app" / "__init__.py"

SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?")


def _read(pattern: str, path: Path) -> str:
    m = re.search(pattern, path.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"no version found in {path}")
    return m.group(1)


def _write(path: Path, pattern: str, repl: str) -> None:
    text = path.read_text(encoding="utf-8")
    new = re.sub(pattern, repl, text, count=1)
    if new == text:
        raise SystemExit(f"pattern not found in {path}")
    path.write_text(new, encoding="utf-8")


def _json_version(path: Path, version: str | None = None) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if version is not None:
        data["version"] = version
        path.write_text(json.dumps(data, indent=2) + "\n",
                        encoding="utf-8")
    return str(data["version"])


def get() -> str:
    return _read(r'__version__\s*=\s*"([^"]+)"', SOURCE)


def targets() -> dict[str, str]:
    return {
        "backend/pyproject.toml": _read(
            r'(?m)^version\s*=\s*"([^"]+)"',
            ROOT / "backend" / "pyproject.toml"),
        "frontend/package.json": _json_version(
            ROOT / "frontend" / "package.json"),
        "custom_components/family_finance/manifest.json": _json_version(
            ROOT / "custom_components" / "family_finance"
            / "manifest.json"),
        "addon/config.yaml": _read(
            r'(?m)^version\s*:\s*"?([^"\n]+)"?',
            ROOT / "addon" / "config.yaml"),
    }


def check() -> int:
    want = get()
    bad = {name: v for name, v in targets().items() if v != want}
    if bad:
        for name, v in bad.items():
            print(f"version drift: {name}={v} != {want}",
                  file=sys.stderr)
        return 1
    print(f"version {want} consistent across {len(targets())} targets")
    return 0


def sync(version: str) -> None:
    _write(ROOT / "backend" / "pyproject.toml",
           r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"')
    _json_version(ROOT / "frontend" / "package.json", version)
    _json_version(ROOT / "custom_components" / "family_finance"
                  / "manifest.json", version)
    _write(ROOT / "addon" / "config.yaml",
           r'(?m)^version\s*:.*', f'version: "{version}"')


def set_version(version: str) -> None:
    if not SEMVER.fullmatch(version):
        raise SystemExit(f"not semver: {version}")
    _write(SOURCE, r'__version__\s*=\s*"[^"]+"',
           f'__version__ = "{version}"')
    sync(version)
    print(f"version set to {version}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "get":
        print(get())
    elif cmd == "check":
        sys.exit(check())
    elif cmd == "sync":
        sync(get())
    elif cmd == "set":
        set_version(sys.argv[2])
    else:
        raise SystemExit(f"usage: version.py [get|check|sync|set X.Y.Z]")
