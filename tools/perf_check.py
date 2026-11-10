"""Performance validation on modest hardware (issue #132).

Seeds ~5 years of realistic ledger data (~50k transactions) into a
scratch database and times the user-facing hot paths directly at the
domain/query layer — where the real cost lives:

  transaction list (page 1) · payee search · category filter ·
  dashboard bundle (accounts + expected movements + projection) ·
  forecast recompute (90d projection)

Prints a budget report; exit code stays 0 unless --strict is passed
(budgets exceeded >3× → 2) — CI runs it report-only, non-gating.

Usage:
  python tools/perf_check.py [--rows 50000] [--repeats 3] [--strict]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import func, insert, or_, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.core.settings import Settings  # noqa: E402
from app.domain import projection, schedule  # noqa: E402
from app.persistence.engine import create_app_engine  # noqa: E402
from app.persistence.migrate import upgrade_head  # noqa: E402
from app.persistence.models import (  # noqa: E402
    Account,
    AccountType,
    Category,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Household,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

# Perf budgets (issue #132): modest-hardware targets. The report shows
# each path's median vs its budget; --strict fails at 3× budget.
BUDGETS_MS = {
    "txn_list_page1": 200,
    "txn_search_q": 300,
    "txn_category_filter": 200,
    "dashboard_bundle": 2000,
    "forecast_recompute": 1000,
}

PAYEES = (
    ["Tesco", "Sainsbury's", "Asda", "Aldi", "Morrisons", "Amazon",
     "Netflix", "Spotify", "British Gas", "Octopus Energy", "Thames Water",
     "Council Tax", "Sky", "Virgin Media", "TFL", "Shell", "BP",
     "Costa", "Greggs", "McDonald's"]
    + [f"Shop {i}" for i in range(180)]
)
STATUSES = [TransactionStatus.reconciled] * 60 + [
    TransactionStatus.cleared] * 30 + [TransactionStatus.pending] * 10


def seed(session: Session, rows: int) -> tuple[int, list[int], list[int]]:
    """Bulk-insert a realistic ~5-year ledger. Returns household id,
    account ids, category ids."""
    hh = Household(name="Perf Household")
    session.add(hh)
    session.flush()
    accounts = [
        Account(household_id=hh.id, name=n, type=AccountType.joint,
                currency="GBP", opening_balance_pence=0,
                opening_date=date(2021, 1, 1))
        for n in ("Joint", "Personal", "Bills")]
    session.add_all(accounts)
    cats = [Category(household_id=hh.id, name=f"Cat {i}")
            for i in range(30)]
    session.add_all(cats)
    session.flush()

    start = date(2021, 1, 1)
    acct_ids = [a.id for a in accounts]
    cat_ids = [c.id for c in cats]
    chunk = 5000
    for off in range(0, rows, chunk):
        n = min(chunk, rows - off)
        batch = []
        for i in range(off, off + n):
            batch.append({
                "account_id": acct_ids[i % len(acct_ids)],
                "date": start + timedelta(days=i * 1825 // rows),
                "amount_pence": -((i * 37) % 20000 + 100)
                if i % 23 else (i * 131) % 300000 + 50000,
                "payee": PAYEES[i % len(PAYEES)],
                "category_id": cat_ids[i % len(cat_ids)],
                "status": STATUSES[i % len(STATUSES)],
                "source": TransactionSource.manual,
                "external_ref": f"perf-{i}",
            })
        session.execute(insert(Transaction), batch)
    # A few hundred expected movements so forecast/stream do real work.
    today = date.today()
    session.execute(insert(ExpectedMovement), [
        {"household_id": hh.id,
         "account_id": acct_ids[i % len(acct_ids)],
         "expected_date": today + timedelta(days=(i * 3) % 90),
         "amount_pence": -((i * 97) % 15000 + 500) if i % 7 else 250000,
         "label": f"Expected {i}",
         "confidence": Confidence.scheduled,
         "source_type": ExpectedSource.manual,
         "source_id": i}
        for i in range(400)])
    session.commit()
    return hh.id, acct_ids, cat_ids


def _time(fn, repeats: int) -> float:
    """Median wall-clock ms over `repeats` runs."""
    ts = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def run(session: Session, household_id: int, acct_ids: list[int],
        cat_ids: list[int], repeats: int) -> dict[str, float]:
    today = date.today()
    first_account = acct_ids[0]

    def txn_page1():
        stmt = (select(Transaction)
                .join(Account, Transaction.account_id == Account.id)
                .where(Account.household_id == household_id)
                .order_by(Transaction.date.desc(), Transaction.id.desc())
                .limit(50))
        return session.execute(stmt).scalars().all()

    def txn_search():
        stmt = (select(Transaction)
                .join(Account, Transaction.account_id == Account.id)
                .where(Account.household_id == household_id,
                       or_(Transaction.payee.like("%Tesco%"),
                           Transaction.payee.like("%Amazon%")))
                .limit(50))
        return session.execute(stmt).scalars().all()

    def txn_cat():
        stmt = (select(Transaction)
                .join(Account, Transaction.account_id == Account.id)
                .where(Account.household_id == household_id,
                       Transaction.category_id == cat_ids[0])
                .limit(50))
        return session.execute(stmt).scalars().all()

    def dashboard():
        session.execute(select(func.count()).select_from(Account).where(
            Account.household_id == household_id)).scalar_one()
        schedule.stream(session, household_id,
                        start=today, end=today + timedelta(days=90))
        acct = session.get(Account, first_account)
        projection.project(session, acct, days=30, from_date=today)

    def forecast():
        acct = session.get(Account, first_account)
        projection.project(session, acct, days=90, from_date=today)

    return {
        "txn_list_page1": _time(txn_page1, repeats),
        "txn_search_q": _time(txn_search, repeats),
        "txn_category_filter": _time(txn_cat, repeats),
        "dashboard_bundle": _time(dashboard, repeats),
        "forecast_recompute": _time(forecast, repeats),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=50_000)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="ff-perf-"))
    settings = Settings.load({"FF_DATA_DIR": str(tmp)})
    url = settings.resolved_database_url
    upgrade_head(url)
    session = sessionmaker(bind=create_app_engine(url))()

    t0 = time.perf_counter()
    hid, acct_ids, cat_ids = seed(session, a.rows)
    seed_ms = (time.perf_counter() - t0) * 1000
    total = session.execute(
        select(func.count()).select_from(Transaction)).scalar_one()
    print(f"seeded {total} transactions in {seed_ms:.0f}ms\n")

    results = run(session, hid, acct_ids, cat_ids, a.repeats)
    print(f"{'check':<22}{'median ms':>10}{'budget':>8}  verdict")
    worst = 0.0
    for name, ms in results.items():
        budget = BUDGETS_MS[name]
        ratio = ms / budget
        worst = max(worst, ratio)
        verdict = ("ok" if ratio <= 1 else "over" if ratio <= 3
                   else "FAIL")
        print(f"{name:<22}{ms:>10.1f}{budget:>8}  {verdict} "
              f"({ratio:.2f}x budget)")
    print()
    session.close()
    if a.strict and worst > 3:
        print("STRICT: budgets exceeded >3× — investigate")
        return 2
    print("report complete (non-gating)" if not a.strict
          else "STRICT: within 3× budgets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
