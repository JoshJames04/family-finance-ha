"""Issue #288 — statement file imports: CSV/OFX/QIF parsing, fingerprint
dedup, preview→commit→rollback batch lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import imports
from app.domain.imports import ImportError_
from app.main import create_app
from app.persistence.models import (
    Account,
    Household,
    Transaction,
    TransactionSource,
)

CSV_HEADER = "Date,Description,Money Out,Money In\n"
CSV_ROWS = (
    "01/04/2025,TESCO STORES,25.40,\n"
    "03/04/2025,SALARY,,1500.00\n"
    "05/04/2025,NETFLIX,10.99,\n"
)

OFX = """<OFX><BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20250401<TRNAMT>-25.40<NAME>TESCO</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20250403<TRNAMT>1500.00<NAME>SALARY<MEMO>monthly</STMTTRN>
</BANKTRANLIST></OFX>"""

QIF = """!Type:Bank
D01/04/2025
T-25.40
PTESCO
^
D03/04/2025
T1500.00
PSALARY
^
"""


class TestCsvParse:
    def test_debit_credit_columns(self) -> None:
        rows = imports.parse_csv(
            (CSV_HEADER + CSV_ROWS).encode(),
            {"date": "Date", "payee": "Description",
             "debit": "Money Out", "credit": "Money In"})
        assert len(rows) == 3
        assert rows[0].amount_pence == -2540
        assert rows[0].payee == "TESCO STORES"
        assert rows[1].amount_pence == 150_000
        assert rows[0].fingerprint.startswith("imp:")

    def test_single_amount_column_indexed_no_header(self) -> None:
        rows = imports.parse_csv(
            b"2025-04-01,SHOP,-12.99\n2025-04-02,SHOP,-3.50\n",
            {"date": 0, "payee": 1, "amount": 2, "skip_header": False})
        assert [r.amount_pence for r in rows] == [-1299, -350]

    def test_bad_mapping_raises(self) -> None:
        with pytest.raises(ImportError_):
            imports.parse_csv((CSV_HEADER + CSV_ROWS).encode(),
                              {"date": "Date", "payee": "Nope",
                               "amount": "Money Out"})


class TestOtherFormats:
    def test_ofx(self) -> None:
        rows = imports.parse_ofx(OFX.encode())
        assert [r.amount_pence for r in rows] == [-2540, 150_000]
        assert rows[1].notes == "monthly"

    def test_qif(self) -> None:
        rows = imports.parse_qif(QIF.encode())
        assert [r.amount_pence for r in rows] == [-2540, 150_000]


def _txn(session: Session, acct: Account, r: imports.ParsedRow) -> None:
    session.add(Transaction(
        account_id=acct.id, date=r.date, amount_pence=r.amount_pence,
        payee=r.payee, source=TransactionSource.file_import,
        external_ref=r.fingerprint))
    session.flush()


class TestDedup:
    def test_fingerprint_marks_existing(
            self, session: Session, household: Household) -> None:
        acct = Account(household_id=household.id, name="A",
                       type="personal", opening_balance_pence=0,
                       opening_date=date(2025, 1, 1))
        session.add(acct)
        session.flush()
        rows = imports.parse_csv(
            (CSV_HEADER + CSV_ROWS).encode(),
            {"date": "Date", "payee": "Description",
             "debit": "Money Out", "credit": "Money In"})
        _txn(session, acct, rows[0])
        imports.mark_duplicates(session, acct.id, rows)
        assert [r.duplicate for r in rows] == [True, False, False]


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def _setup(self, client: TestClient) -> tuple[dict[str, str], int]:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", headers=h, json={
            "name": "Current", "type": "personal",
            "opening_balance": 100_000,
            "opening_date": "2025-01-01"}).json()["id"]
        return h, aid

    def _upload(self, client: TestClient, h: dict[str, str],
                aid: int, content: bytes = b"") -> dict:
        r = client.post("/api/v1/imports/preview", headers=h,
                        data={"account_id": str(aid), "fmt": "csv",
                              "mapping": '{"date":"Date",'
                              '"payee":"Description",'
                              '"debit":"Money Out","credit":"Money In"}'},
                        files={"file": ("stmt.csv",
                                        content
                                        or (CSV_HEADER + CSV_ROWS)
                                        .encode(), "text/csv")})
        assert r.status_code == 201, r.json()
        return r.json()

    def test_preview_commit_dedup_rollback(
            self, client: TestClient) -> None:
        h, aid = self._setup(client)
        p = self._upload(client, h, aid)
        assert p["import_row"]["rows_total"] == 3
        assert not any(r["duplicate"] for r in p["rows"])
        iid = p["import_row"]["id"]

        c = client.post(f"/api/v1/imports/{iid}/commit", headers=h)
        assert c.status_code == 200
        assert c.json()["status"] == "committed"
        assert c.json()["rows_imported"] == 3

        # Re-importing the same file previews every row as duplicate
        p2 = self._upload(client, h, aid)
        assert all(r["duplicate"] for r in p2["rows"])
        c2 = client.post(f"/api/v1/imports/{p2['import_row']['id']}/commit",
                         headers=h)
        assert c2.json()["rows_imported"] == 0
        assert c2.json()["rows_skipped"] == 3
        txns = client.get("/api/v1/transactions", headers=h,
                          params={"size": 100}).json()["items"]
        assert len([t for t in txns
                    if t["source"] == "file_import"]) == 3

        rb = client.post(f"/api/v1/imports/{iid}/rollback", headers=h)
        assert rb.json()["status"] == "rolled_back"
        txns = client.get("/api/v1/transactions", headers=h,
                          params={"size": 100}).json()["items"]
        assert not [t for t in txns if t["source"] == "file_import"]

        hist = client.get("/api/v1/imports", headers=h).json()
        assert hist["total"] == 2

    def test_commit_twice_rejected(self, client: TestClient) -> None:
        h, aid = self._setup(client)
        iid = self._upload(client, h, aid)["import_row"]["id"]
        assert client.post(f"/api/v1/imports/{iid}/commit",
                           headers=h).status_code == 200
        assert client.post(f"/api/v1/imports/{iid}/commit",
                           headers=h).status_code == 409

    def test_ofx_upload(self, client: TestClient) -> None:
        h, aid = self._setup(client)
        r = client.post("/api/v1/imports/preview", headers=h,
                        data={"account_id": str(aid), "fmt": "ofx"},
                        files={"file": ("stmt.ofx", OFX.encode(),
                                        "application/octet-stream")})
        assert r.status_code == 201
        assert len(r.json()["rows"]) == 2
