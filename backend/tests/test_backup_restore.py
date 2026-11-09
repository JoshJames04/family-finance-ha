"""Issue #24 — backup, restore, rotation, failed-migration recovery.

AC: backup→wipe→restore→integrity-check produces identical data; a
forced failed migration restores the pre-migration snapshot and the app
reports unhealthy instead of corrupting data.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.settings import Settings
from app.main import create_app
from app.persistence import backup
from app.persistence.engine import create_app_engine, integrity_check
from app.persistence.migrate import upgrade_head

ADMIN = {"X-Admin-Token": "s3cret"}


def _settings(tmp_path: Path, **env: str) -> Settings:
    return Settings.load(
        {"FF_DATA_DIR": str(tmp_path), "FF_ADMIN_TOKEN": "s3cret",
         **env})


@pytest.fixture()
def db(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'd.db').as_posix()}"
    upgrade_head(url)
    engine = create_app_engine(url)
    yield engine, tmp_path / "d.db"
    engine.dispose()


def _rows(engine, table: str) -> list[tuple]:
    with engine.connect() as c:
        return c.execute(text(f"SELECT * FROM {table}")).fetchall()


class TestSnapshots:
    def test_wipe_restore_identical(self, db) -> None:
        """AC: backup → wipe → restore → integrity check = identical."""
        engine, db_path = db
        factory = sessionmaker(bind=engine)
        with factory() as s:
            s.execute(text(
                "INSERT INTO household (name) VALUES ('Keep')"))
            s.commit()
        before = _rows(engine, "household")

        snap = backup.create_snapshot(engine, db_path.parent / "b")
        assert backup.verify_snapshot(snap)

        engine.dispose()
        db_path.unlink()  # wipe
        backup.restore_from(snap, db_path)

        engine2 = create_app_engine(
            f"sqlite:///{db_path.as_posix()}")
        assert integrity_check(engine2)
        assert _rows(engine2, "household") == before
        engine2.dispose()

    def test_rotate_keeps_newest(self, db) -> None:
        engine, db_path = db
        bdir = db_path.parent / "b"
        from datetime import UTC, datetime, timedelta
        base = datetime(2026, 1, 1, tzinfo=UTC)
        snaps = [
            backup.create_snapshot(engine, bdir,
                                   now=base + timedelta(hours=i))
            for i in range(5)]
        assert backup.rotate(bdir, keep=2) == 3
        remaining = [p.name for p in backup.list_snapshots(bdir)]
        assert remaining == [p.name for p in snaps[-2:]]
        assert backup.latest_snapshot(bdir) == snaps[-1]

    def test_marker_accepts_pending_prefix(self, tmp_path: Path) -> None:
        """#342 — marker roundtrip accepts pending/ uploads and
        still rejects anything else."""
        marker = tmp_path / "restore.pending"
        backup.request_restore(marker, "pending/ff-20261109T000000.db")
        assert backup.take_pending_restore(
            marker) == "pending/ff-20261109T000000.db"
        backup.request_restore(marker, "ff-20261109T000000.db")
        assert backup.take_pending_restore(
            marker) == "ff-20261109T000000.db"
        with pytest.raises(ValueError):
            backup.request_restore(marker, "pending/../evil.db")

    def test_verify_rejects_garbage(self, tmp_path: Path) -> None:
        bad = tmp_path / "ff-20260101T000000.db"
        bad.write_bytes(b"not a database")
        assert backup.verify_snapshot(bad) is False
        assert backup.verify_snapshot(tmp_path / "evil.db") is False


class TestAdminEndpoints:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(_settings(tmp_path))) as c:
            yield c

    def test_admin_gate_fail_closed(self, tmp_path: Path) -> None:
        """No token configured → always 403; wrong token → 403."""
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            assert c.get("/api/v1/admin/backups").status_code == 403
        with TestClient(create_app(_settings(
                tmp_path / "x"))) as c:
            assert c.get("/api/v1/admin/backups").status_code == 403
            assert c.get("/api/v1/admin/backups",
                         headers={"X-Admin-Token": "wrong"}
                         ).status_code == 403

    def test_admin_gate_accepts_profile_header(
            self, tmp_path: Path) -> None:
        """Standalone compat: X-Profile-Id of an active admin profile
        opens the admin gate; a member profile does not. Ingress-mode
        compat headers stay ignored."""
        s = _settings(tmp_path)
        with TestClient(create_app(s)) as c:
            hid = c.post("/api/v1/households",
                         json={"name": "H"}).json()["id"]
            h = {"X-Household-Id": str(hid)}
            admin = c.post("/api/v1/profiles", json={
                "display_name": "Mum", "role": "admin"},
                headers=h).json()
            member = c.post("/api/v1/profiles", json={
                "display_name": "Kid", "role": "member"},
                headers=h).json()
            assert c.get("/api/v1/admin/backups", headers={
                **h, "X-Profile-Id": str(admin["id"])}
            ).status_code == 200
            assert c.get("/api/v1/admin/backups", headers={
                **h, "X-Profile-Id": str(member["id"])}
            ).status_code == 403
            # Garbage in the header is not an admin identity.
            assert c.get("/api/v1/admin/backups", headers={
                **h, "X-Profile-Id": "nope"}
            ).status_code == 403

    def test_list_newest_first_with_taken_at(
            self, tmp_path: Path) -> None:
        """#371 — the list is newest-first, carries a parsed
        timestamp, and orders same-second seq-suffixed names
        chronologically (``-1`` lexes before ``.db``)."""
        s = _settings(tmp_path)
        with TestClient(create_app(s)) as c:
            hid = c.post("/api/v1/households",
                         json={"name": "H"}).json()["id"]
            h = {"X-Household-Id": str(hid), **ADMIN}
            names = [c.post("/api/v1/admin/backups",
                            headers=h).json()["name"]
                     for _ in range(2)]
            items = c.get("/api/v1/admin/backups",
                          headers=h).json()
            assert [i["name"] for i in items] == names[::-1]
            assert all(i["taken_at"] for i in items)

    def test_backup_restore_flow(self, tmp_path: Path) -> None:
        """Manual backup + pending restore applied at next boot."""
        s = _settings(tmp_path)
        with TestClient(create_app(s)) as c:
            hid = c.post("/api/v1/households",
                         json={"name": "H1"}).json()["id"]
            h = {"X-Household-Id": str(hid), **ADMIN}
            r = c.post("/api/v1/admin/backups", headers=h)
            assert r.status_code == 201
            name = r.json()["name"]
            assert r.json()["ok"] is True
            assert (s.resolved_backup_dir / name).exists()
            # post-snapshot mutation must vanish after restore
            c.post("/api/v1/households", json={"name": "H2"})
            r = c.post("/api/v1/admin/restore", json={"name": name},
                       headers=h)
            assert r.status_code == 202
            assert s.restore_marker.is_file()
            # audited
            r = c.post("/api/v1/admin/restore",
                       json={"name": "../evil"}, headers=h)
            assert r.status_code in (404, 422)

        db_file = tmp_path / "family_finance.db"
        conn = sqlite3.connect(db_file)
        try:
            assert conn.execute(
                "SELECT count(*) FROM household").fetchone()[0] == 2
        finally:
            conn.close()

        with TestClient(create_app(s)):
            pass  # boot applies the pending restore
        assert not s.restore_marker.exists()
        conn = sqlite3.connect(db_file)
        try:
            assert conn.execute(
                "SELECT count(*) FROM household").fetchone()[0] == 1
        finally:
            conn.close()

    def test_download_and_upload_restore(self, tmp_path: Path) -> None:
        """Download streams a snapshot; uploading one queues a restore
        (issue #328)."""
        s = _settings(tmp_path)
        with TestClient(create_app(s)) as c:
            hid = c.post("/api/v1/households",
                         json={"name": "H1"}).json()["id"]
            h = {"X-Household-Id": str(hid), **ADMIN}
            name = c.post("/api/v1/admin/backups",
                          headers=h).json()["name"]
            dl = c.get(f"/api/v1/admin/backups/{name}/download",
                       headers=h)
            assert dl.status_code == 200
            assert dl.content[:15] == b"SQLite format 3"
            # Pattern-guarded names only — no traversal, no other files.
            assert c.get("/api/v1/admin/backups/evil.db/download",
                         headers=h).status_code == 404
            assert c.get("/api/v1/admin/backups/"
                         "ff-19990101T000000.db/download",
                         headers=h).status_code == 404
            # Upload → verify → marker; then a corrupt upload is refused.
            up = c.post("/api/v1/admin/restore/upload", headers=h,
                        files={"file": ("snap.db", dl.content)})
            assert up.status_code == 202
            assert s.restore_marker.is_file()
            # issue #342 — uploads live under pending/ and never
            # appear in the backups list.
            marker_name = s.restore_marker.read_text().strip()
            assert marker_name.startswith("pending/ff-")
            assert (s.resolved_backup_dir / marker_name).is_file()
            listed = c.get("/api/v1/admin/backups", headers=h).json()
            names = [b["name"] for b in
                     (listed["items"] if isinstance(listed, dict)
                      else listed)]
            assert marker_name not in names
            assert not any(n.startswith("pending") for n in names)
            s.restore_marker.unlink()
            bad = c.post("/api/v1/admin/restore/upload", headers=h,
                         files={"file": ("bad.db", b"not sqlite")})
            assert bad.status_code == 422
            assert not s.restore_marker.exists()


class TestFailedMigration:
    def test_failed_upgrade_restores_and_degrades(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Forced migration failure → snapshot restored + 503 readyz."""
        s = _settings(tmp_path)
        with TestClient(create_app(s)) as c:
            c.post("/api/v1/households", json={"name": "H1"})
        db_file = tmp_path / "family_finance.db"
        assert db_file.exists()

        import app.main as m
        monkeypatch.setattr(m, "upgrade_head",
                            lambda _url: (_ for _ in ()).throw(
                                RuntimeError("boom")))
        with TestClient(create_app(s)) as c:
            r = c.get("/readyz")
            assert r.status_code == 503
            comps = r.json()["components"]
            assert comps["boot"]["ok"] is False
            assert "boom" in comps["boot"]["detail"]
        # data preserved by the auto-restored snapshot
        conn = sqlite3.connect(db_file)
        try:
            assert conn.execute(
                "SELECT name FROM household").fetchone()[0] == "H1"
        finally:
            conn.close()
