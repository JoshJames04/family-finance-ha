"""v1.0 production-readiness gate (issue #134).

Runs every mechanically-verifiable gate item and prints a checklist:
PASS / FAIL / OPERATOR (needs a human: real-data sign-offs, the tag
itself). Exit 0 when all machine checks pass; operator items are
listed, never silently skipped.

Usage: python tools/release_gate.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "backend" / "scripts"))

PASS, FAIL, OPERATOR = "PASS", "FAIL", "OPERATOR"


def _alembic_cfg(db_url: str | None = None):
    """Alembic config anchored at the repo — the ini's script_location
    is relative to backend/."""
    import os

    from alembic.config import Config  # noqa: E402
    cfg = Config(str(REPO / "backend" / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(REPO / "backend" / "migrations"))
    if db_url:
        cfg.set_main_option("sqlalchemy.url", db_url)
    os.chdir(REPO / "backend")
    return cfg


def check_alembic_heads() -> tuple[str, str]:
    from alembic.script import ScriptDirectory  # noqa: E402
    heads = list(ScriptDirectory.from_config(_alembic_cfg()).get_heads())
    return (PASS if len(heads) == 1 else FAIL, f"heads: {heads}")


def check_migration_roundtrip() -> tuple[str, str]:
    from alembic import command  # noqa: E402
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _alembic_cfg(f"sqlite:///{tmp}/rt.db")
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "-1")
        command.upgrade(cfg, "head")
    return PASS, "upgrade->downgrade->upgrade clean on scratch DB"


def check_restore() -> tuple[str, str]:
    import verify_restore  # noqa: E402
    with tempfile.TemporaryDirectory() as tmp:
        result = verify_restore.verify_restore_cycle(Path(tmp))
    ok = all(v is True or isinstance(v, int) for v in result.values())
    return (PASS if ok else FAIL, json.dumps(result))


def check_version() -> tuple[str, str]:
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "version.py"), "check"],
        cwd=REPO, capture_output=True, text=True)
    return (PASS if r.returncode == 0 else FAIL,
            (r.stdout + r.stderr).strip()[:120])


def check_openapi_sync() -> tuple[str, str]:
    from app.main import create_app  # noqa: E402
    live = json.dumps(create_app().openapi(), sort_keys=True)
    saved_path = REPO / "backend" / "openapi.json"
    if not saved_path.exists():
        return FAIL, "backend/openapi.json missing"
    saved = json.dumps(json.loads(saved_path.read_text()),
                       sort_keys=True)
    return (PASS if live == saved else FAIL,
            "generated spec identical to tracked openapi.json"
            if live == saved else
            "openapi.json drifted — regenerate via npm run gen:api")


def check_docs() -> tuple[str, str]:
    required = ["ARCHITECTURE.md", "DATA-MODEL.md", "MIGRATION.md",
                "PERFORMANCE.md", "RELEASE.md", "ROADMAP.md",
                "SECURITY.md", "HA-DEPLOYMENT.md"]
    missing = [d for d in required
               if not (REPO / "docs" / d).exists()]
    return (PASS if not missing else FAIL,
            "all required docs present" if not missing
            else f"missing: {missing}")


def check_backup_api() -> tuple[str, str]:
    """Failed-upgrade recovery evidence: the restore verifier seeds,
    snapshots, wipes, restores and equality-checks — covered by
    check_restore; this confirms the migration suite exists."""
    t = REPO / "backend" / "tests" / "test_restore_verification.py"
    return (PASS if t.exists() else FAIL,
            "restore-verification suite present" if t.exists()
            else "test_restore_verification.py missing")


MACHINE_CHECKS = [
    ("alembic single head", check_alembic_heads),
    ("migration upgrade/downgrade", check_migration_roundtrip),
    ("backup->wipe->restore + integrity", check_restore),
    ("version consistency", check_version),
    ("openapi.json in sync", check_openapi_sync),
    ("docs complete", check_docs),
    ("restore-verification suite", check_backup_api),
]

OPERATOR_ITEMS = [
    "Full CI pyramid green on develop (unit, integration, contract, "
    "e2e, migration, determinism, security, HA matrix) - gh run list",
    "Migration acceptance #56 - defect-B flag resolved + parallel-run "
    "month + MIGRATION.md section-7 sign-off",
    "Perf budget reviewed - docs/PERFORMANCE.md (#132)",
    "v1.0.0 tag + release via docs/RELEASE.md sequence",
]


def main() -> int:
    print("=== v1.0 readiness gate (issue #134) ===\n")
    failures = 0
    for name, fn in MACHINE_CHECKS:
        try:
            status, detail = fn()
        except Exception as e:  # a check that can't run is a FAIL
            status, detail = FAIL, f"{type(e).__name__}: {e}"
        print(f"[{status:>8}] {name:<38} {detail}")
        if status == FAIL:
            failures += 1
    print()
    for item in OPERATOR_ITEMS:
        print(f"[OPERATOR] {item}")
    print(f"\n{failures} machine check(s) failed"
          if failures else "\nAll machine checks pass — operator items remain")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
