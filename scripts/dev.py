#!/usr/bin/env python3
"""Dev environment helper — thin wrapper over docker compose.

Usage:
    python scripts/dev.py up      # build + start the dev stack
    python scripts/dev.py down    # stop it
    python scripts/dev.py seed    # write synthetic fixtures into the data volume
    python scripts/dev.py test    # run backend tests locally (venv)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sh(*args: str) -> int:
    print("+", " ".join(args))
    return subprocess.call(args, cwd=ROOT)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "up"
    if cmd == "up":
        return sh("docker", "compose", "up", "--build", "-d")
    if cmd == "down":
        return sh("docker", "compose", "down")
    if cmd == "seed":
        # FF_DATA_DIR=/data is set in docker-compose; no path arg needed
        # (positional paths get mangled by MSYS on Windows).
        return sh("docker", "compose", "exec", "-T", "backend",
                  "python", "-m", "app.seed")
    if cmd == "test":
        return sh(sys.executable, "-m", "pytest", "backend/tests", "-q")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
