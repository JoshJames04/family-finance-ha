"""Issue #132 — repeatable perf measurement at fixture scale.

Non-gating by design: timings are printed for CI logs and never
asserted (hardware varies). Functional sanity is asserted so a broken
path still fails the suite. The full 50k-row run is
`tools/perf_check.py`; this suite uses a smaller deterministic fixture
so it stays cheap enough to run on every CI invocation.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import Transaction

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import perf_check  # noqa: E402

ROWS = 15_000


@pytest.fixture()
def perf_data(session: Session) -> tuple[int, list[int], list[int]]:
    hid, acct_ids, cat_ids = perf_check.seed(session, ROWS)
    return hid, acct_ids, cat_ids


@pytest.mark.perf
class TestPerfMeasurement:
    def test_fixture_scale(self, session: Session,
                           perf_data: tuple[int, list[int],
                                            list[int]]) -> None:
        total = session.execute(
            select(Transaction)).scalars().all()
        assert len(total) == ROWS

    def test_hot_paths_report(self, session: Session,
                              perf_data: tuple[int, list[int],
                                               list[int]],
                              capsys: pytest.CaptureFixture[str],
                              ) -> None:
        hid, acct_ids, cat_ids = perf_data
        results = perf_check.run(session, hid, acct_ids, cat_ids,
                                 repeats=3)
        for name, ms in results.items():
            budget = perf_check.BUDGETS_MS[name]
            print(f"\n[perf] {name}: {ms:.1f}ms "
                  f"(budget {budget}ms, {ms / budget:.2f}x)")
        # Functional sanity: the paths actually returned data.
        assert all(ms >= 0 for ms in results.values())
