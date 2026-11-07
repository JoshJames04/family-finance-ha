"""Reconciliation report, review queue and retirement gate
(issue #49): golden report on the defect-bearing fixture, flag
lifecycle, audited resolution."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import create_app
from app.migration.pipeline import commit_batch, create_batch
from app.migration.report import reconciliation_report, retirement_gate
from app.persistence.models import (
    AuditEvent,
    Household,
    ImportStatus,
    MigrationFlag,
)
from tests.fixtures.synthetic_workbook import build_workbook


@pytest.fixture()
def wb_path(tmp_path: Path) -> Path:
    p = tmp_path / "synthetic.xlsx"
    build_workbook().save(p)
    return p


@pytest.fixture()
def batch(session: Session, household: Household, wb_path: Path):
    b, _ = create_batch(session, household.id, wb_path, "tester")
    commit_batch(session, b, [])
    session.commit()
    return b


class TestReport:
    def test_golden_report_all_six_defects(
        self, session: Session, batch,
    ) -> None:
        report = reconciliation_report(session, batch)
        assert report["batch"]["status"] == ImportStatus.committed
        # fixture carries defects A–F — all six detected
        detected = set(report["defect_flags"]["by_defect"])
        assert detected == {"A", "B", "C", "D", "E", "F"}
        assert report["defect_flags"]["unresolved"] == \
            report["defect_flags"]["total"] > 0
        # every staged sheet accounted for, none silently dropped
        assert report["sheets"]
        assert sum(s["records"] for s in
                   report["sheets"].values()) == \
            report["batch"]["rows_read"]
        assert report["retirement_ready"] is False

    def test_report_has_no_cell_values(
        self, session: Session, batch,
    ) -> None:
        import json
        payload = json.dumps(reconciliation_report(session, batch))
        assert "sha256" in payload
        # structural counts only — no staged raw/interpreted strings
        assert "interpreted" not in payload
        assert "raw_value" not in payload


class TestRetirementGate:
    def test_gate_blocks_on_unresolved(
        self, session: Session, batch,
    ) -> None:
        gate = retirement_gate(session, batch)
        assert gate["committed"] is True
        assert gate["unresolved_flags"] > 0
        assert gate["ready"] is False

    def test_gate_opens_when_all_resolved(
        self, session: Session, batch,
    ) -> None:
        for f in session.execute(select(MigrationFlag)).scalars():
            f.status = "resolved"
        session.commit()
        assert retirement_gate(session, batch)["ready"] is True


class TestApi:
    def test_resolve_audited(
        self, tmp_path: Path, wb_path: Path,
    ) -> None:
        from sqlalchemy.orm import sessionmaker

        from app.core.settings import Settings
        from app.persistence.engine import create_app_engine

        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        app = create_app(settings)
        url = settings.resolved_database_url
        with TestClient(app):  # lifespan runs migrations
            factory = sessionmaker(bind=create_app_engine(url))
            with factory() as s:
                hh = Household(name="T")
                s.add(hh)
                s.flush()
                batch, _ = create_batch(s, hh.id, wb_path, "t")
                flag = MigrationFlag(batch_id=batch.id, defect_id="A",
                                     status="detected", detail="{}")
                s.add(flag)
                s.commit()
                fid, hid, bid = flag.id, hh.id, batch.id
            h = {"X-Household-Id": str(hid)}
            with TestClient(app) as client:
                r = client.post(
                    f"/api/v1/migration/flags/{fid}/resolve",
                    headers=h,
                    json={"status": "resolved",
                          "note": "verified against payslip"})
                assert r.status_code == 200, r.text
                assert r.json()["status"] == "resolved"
                g = client.get(
                    f"/api/v1/migration/batches/{bid}"
                    "/retirement-gate", headers=h)
                assert g.status_code == 200
            with factory() as s:
                ev = s.execute(select(AuditEvent).where(
                    AuditEvent.action == "migration_flag.resolve")
                ).scalar_one()
                assert ev.entity_id == fid
