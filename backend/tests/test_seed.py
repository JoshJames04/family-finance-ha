"""Seed fixture tests — synthetic data policy enforcement."""

import json
import subprocess
import sys
from pathlib import Path


def test_seed_writes_fixture(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "app.seed", str(tmp_path)], check=True
    )
    data = json.loads((tmp_path / "seed.json").read_text())
    assert data["household"]["currency"] == "GBP"
    assert len(data["accounts"]) >= 2
    assert all(a["opening_balance_pence"] >= 0 for a in data["accounts"])


def test_seed_is_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    subprocess.run([sys.executable, "-m", "app.seed", str(a)], check=True)
    subprocess.run([sys.executable, "-m", "app.seed", str(b)], check=True)
    assert (a / "seed.json").read_text() == (b / "seed.json").read_text()
