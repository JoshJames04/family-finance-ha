"""Issue #131 — backup is only real when restored: the scripted
seed→snapshot→wipe→restore→equality cycle runs inside the backend CI
job via this wrapper (and standalone via `scripts/verify_restore.py`).
"""
from __future__ import annotations

from pathlib import Path

from scripts.verify_restore import verify_restore_cycle


def test_full_dataset_cycle(tmp_path: Path) -> None:
    report = verify_restore_cycle(tmp_path / "cycle")
    assert report["ok"] is True
    assert report["rows"] > 20  # the seed actually populated tables
