"""Restore-verification cycle (issue #131).

seed → snapshot → wipe → restore → dataset-equality + integrity_check.

Runnable standalone (``python scripts/verify_restore.py [workdir]``,
non-zero exit on any mismatch) and importable — the pytest wrapper in
``tests/test_restore_verification.py`` runs it in the backend CI job.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.persistence import backup  # noqa: E402
from app.persistence.engine import create_app_engine, integrity_check  # noqa: E402
from app.persistence.migrate import upgrade_head  # noqa: E402
from app.persistence.models import (  # noqa: E402
    Account,
    AccountType,
    Base,
    Budget,
    BudgetLine,
    BudgetLineSide,
    BudgetPeriodType,
    Category,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    RecurringCommitment,
    ReportingPeriod,
    Role,
    Transaction,
    TransactionSource,
    TransactionStatus,
    Transfer,
    UserProfile,
)

T0 = date(2026, 1, 1)


def seed(session) -> None:
    """A realistic cross-section of every major table."""
    hh = Household(name="Verify Household")
    session.add(hh)
    session.flush()
    sam = UserProfile(household_id=hh.id, display_name="Alice",
                       role=Role.admin)
    bob = UserProfile(household_id=hh.id, display_name="Bob",
                       role=Role.member)
    session.add_all([sam, bob])
    session.flush()

    current = Account(household_id=hh.id, name="Joint Current",
                      type=AccountType.joint, owner_profile_id=sam.id,
                      opening_date=T0)
    savings = Account(household_id=hh.id, name="Savings",
                      type=AccountType.savings, owner_profile_id=bob.id,
                      opening_date=T0)
    session.add_all([current, savings])
    session.flush()

    groceries = Category(household_id=hh.id, name="Groceries")
    bills = Category(household_id=hh.id, name="Bills")
    session.add_all([groceries, bills])
    session.flush()

    tx = [
        Transaction(account_id=current.id, date=T0 + timedelta(days=i),
                    amount_pence=amt, payee=payee,
                    category_id=groceries.id if amt < 0 else None,
                    status=TransactionStatus.cleared,
                    source=TransactionSource.manual)
        for i, (amt, payee) in enumerate([
            (250_000, "Salary"), (-8_540, "Tesco"), (-12_100, "Aldi"),
            (-4_999, "Corner shop"), (30_000, "Refund in"),
            (-1_700, "Petrol"), (-950, "Coffee"), (-55_000, "Rent"),
        ])]
    session.add_all(tx)
    tr = Transfer(household_id=hh.id, notes="monthly sweep")
    session.add(tr)
    session.flush()
    session.add_all([
        Transaction(account_id=current.id, date=T0, amount_pence=-50_000,
                    payee="Sweep to savings", status=TransactionStatus.cleared,
                    source=TransactionSource.manual, transfer_id=tr.id),
        Transaction(account_id=savings.id, date=T0, amount_pence=50_000,
                    payee="Sweep from current",
                    status=TransactionStatus.cleared,
                    source=TransactionSource.manual, transfer_id=tr.id),
    ])

    c = RecurringCommitment(household_id=hh.id, name="Council tax",
                            account_id=current.id, category_id=bills.id,
                            amount_pence=-15_200, frequency=Frequency.monthly,
                            next_date=T0 + timedelta(days=30))
    session.add(c)
    budget = Budget(household_id=hh.id, name="Jan 2026",
                    period_type=BudgetPeriodType.monthly,
                    start_date=T0, end_date=T0 + timedelta(days=30))
    session.add(budget)
    session.flush()
    session.add(BudgetLine(budget_id=budget.id, category_id=groceries.id,
                           side=BudgetLineSide.expense,
                           original_pence=60_000, revised_pence=60_000))
    session.add(ReportingPeriod(household_id=hh.id, label="2026-01",
                                start_date=T0,
                                end_date=T0 + timedelta(days=30)))
    session.add(ExpectedMovement(
        household_id=hh.id, account_id=current.id,
        expected_date=T0 + timedelta(days=30), amount_pence=-15_200,
        label="Council tax", confidence=Confidence.scheduled,
        source_type=ExpectedSource.commitment, source_id=1))
    session.commit()


def dump_all(engine: Engine) -> dict[str, list[tuple]]:
    """Every user table → rows ordered by primary key — the canonical
    dataset-equality snapshot the cycle compares before/after."""
    out: dict[str, list[tuple]] = {}
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            pk = list(table.primary_key.columns)
            stmt = select(table).order_by(*pk) if pk else select(table)
            out[table.name] = [tuple(r) for r in conn.execute(stmt)]
    return out


def verify_restore_cycle(workdir: Path) -> dict[str, int | bool]:
    """seed → snapshot → wipe → restore → equality. Raises on failure;
    returns a report dict on success."""
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / "verify.db"
    url = f"sqlite:///{db_path.as_posix()}"
    upgrade_head(url)
    engine = create_app_engine(url)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        seed(s)
    before = dump_all(engine)
    n_rows = sum(len(r) for r in before.values())

    snap = backup.create_snapshot(engine, workdir / "backups")
    assert backup.verify_snapshot(snap), f"integrity_check: {snap}"
    engine.dispose()
    db_path.unlink()  # wipe
    backup.restore_from(snap, db_path)

    engine2 = create_app_engine(url)
    try:
        assert integrity_check(engine2), "integrity_check post-restore"
        after = dump_all(engine2)
    finally:
        engine2.dispose()

    diffs = [t for t in before if before[t] != after.get(t)]
    assert not diffs, f"dataset mismatch in tables: {diffs}"
    return {"tables": len(before), "rows": n_rows, "ok": True}


def main() -> int:
    workdir = (Path(sys.argv[1]) if len(sys.argv) > 1
               else Path(tempfile.mkdtemp(prefix="ff-verify-")))
    try:
        report = verify_restore_cycle(workdir)
    except Exception as exc:  # noqa: BLE001 — the report IS the gate
        print(f"FAIL {exc}")
        return 1
    print(f"OK {report['tables']} tables, {report['rows']} rows "
          f"identical after wipe+restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
