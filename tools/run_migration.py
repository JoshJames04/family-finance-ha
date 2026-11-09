"""Workbook migration acceptance run (issue #56) — LOCAL ONLY.

Executes the docs/MIGRATION.md §7 sequence against a chosen data dir:

  pre-retirement backup → stage → commit → reconcile → rollback test
  → re-commit → retirement gate → sign-off block for MIGRATION.md §7

The workbook never leaves this machine and nothing it prints carries
cell values — structural counts only.

Usage (workbook path stays out of shell history where possible):
  python tools/run_migration.py --data-dir ./data/acceptance \
      --workbook <path> --employer <name> --loan-party <name>
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.core.settings import Settings
from app.migration.pipeline import commit_batch, create_batch, rollback_batch
from app.migration.report import reconciliation_report, retirement_gate
from app.migration.wiring import build_extractors
from app.persistence.engine import create_app_engine
from app.persistence.migrate import upgrade_head
from app.persistence.models import (
    Account,
    AccountType,
    Employment,
    Household,
    Role,
    Transaction,
    TransactionStatus,
    UserProfile,
)
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workbook", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--employer", required=True)
    p.add_argument("--loan-party", required=True)
    p.add_argument("--owner-name", default="Owner")
    p.add_argument("--member2-name", default="Member2")
    p.add_argument("--jurisdiction", default="england_wales")
    p.add_argument("--actor", default="migration-runner")
    return p.parse_args()


def main() -> int:
    a = _args()
    data_dir = Path(a.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings.load({"FF_DATA_DIR": str(data_dir)})
    url = settings.resolved_database_url
    upgrade_head(url)
    s = sessionmaker(bind=create_app_engine(url))()

    spec = json.loads(
        (REPO / "backend/tests/fixtures/workbook-structure.json")
        .read_text())
    name_map = json.loads(
        (REPO / "data/migration-map.local.json")
        .read_text())["sheet_aliases"]

    # --- pre-retirement backup ------------------------------------------
    db_path = settings.database_path
    backup_dir = settings.resolved_backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    if db_path and db_path.exists():
        snap = backup_dir / (
            f"pre-migration-{datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.copy2(db_path, snap)
        print(f"backup: {snap.name}")
    else:
        print("backup: skipped (fresh database)")

    # --- identities ------------------------------------------------------
    hh = s.execute(select(Household)).scalars().first()
    if hh is None:
        hh = Household(name="Household")
        s.add(hh)
        s.flush()
    owner = s.execute(select(UserProfile).where(
        UserProfile.household_id == hh.id)).scalars().first()
    if owner is None:
        owner = UserProfile(household_id=hh.id,
                            display_name=a.owner_name, role=Role.admin)
        s.add(owner)
    member2 = UserProfile(household_id=hh.id,
                          display_name=a.member2_name, role=Role.member)
    s.add(member2)
    s.flush()
    emp = s.execute(select(Employment)).scalars().first()
    if emp is None:
        emp = Employment(household_id=hh.id, profile_id=owner.id,
                         employer=a.employer,
                         jurisdiction=a.jurisdiction, ni_category="A",
                         student_loan_plan="plan2",
                         pension_mode="net_pay", pension_employee_bps=0,
                         pension_fixed_pence=0, contracted_hours=37.5,
                         payday_rule="last_friday")
        s.add(emp)
    acct = s.execute(select(Account)).scalars().first()
    if acct is None:
        acct = Account(household_id=hh.id, name="Imported",
                       type=AccountType.personal, currency="GBP",
                       opening_balance_pence=0,
                       opening_date=date(2024, 1, 1))
        s.add(acct)
    s.commit()

    extractors = build_extractors(
        spec=spec, real_names=name_map, household_id=hh.id,
        owner_profile_id=owner.id, member2_profile_id=member2.id,
        employment_id=emp.id, account_id=acct.id,
        jurisdiction=a.jurisdiction, loan_party=a.loan_party,
        workbook_path=a.workbook)

    # --- stage → commit → reconcile --------------------------------------
    batch, staged = create_batch(s, hh.id, Path(a.workbook), a.actor)
    s.commit()
    imported = commit_batch(s, batch, extractors)
    s.commit()
    report = reconciliation_report(s, batch)
    print(f"staged: {staged} records · imported: {imported}")
    print(f"sheets: {len(report['sheets'])} · entities: "
          f"{sum(report['entities_created'].values())}")
    print(f"flags: {report['defect_flags']['by_defect']} "
          f"(unresolved: {report['defect_flags']['unresolved']})")

    # --- rollback test → re-commit ----------------------------------------
    voided = rollback_batch(s, batch)
    s.commit()
    live = s.execute(select(func.count(Transaction.id)).where(
        Transaction.status != TransactionStatus.void)).scalar_one()
    print(f"rollback: voided {voided} · live txns now {live}")
    reimported = commit_batch(s, batch, extractors)
    s.commit()
    print(f"re-commit: {reimported} records re-mapped")

    # --- retirement gate --------------------------------------------------
    gate = retirement_gate(s, batch)
    print(f"gate: committed={gate['committed']} "
          f"unresolved={gate['unresolved_flags']} "
          f"flagged_records={gate['flagged_records']} "
          f"ready={gate['ready']}")
    print("\n--- sign-off block for docs/MIGRATION.md §7 ---")
    print(f"batch: {batch.id} · sha256: {batch.sha256[:12]}…")
    print("parallel-run month: pending user checklist")
    print("retirement date: pending sign-off")
    s.close()
    return 0 if gate["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
