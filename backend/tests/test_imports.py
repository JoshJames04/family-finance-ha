"""Issue #288 — statement file imports: CSV/OFX/QIF parsing, fingerprint
dedup, preview→commit→rollback batch lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
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
    TransactionStatus,
    UserProfile,
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
        rows = imports.parse_qif(QIF.replace("^\n", "^\n\n").encode())
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

    def test_scoped_import_history(self, client: TestClient) -> None:
        h, aid = self._setup(client)
        with Session(client.app.state.engine) as session:
            owner = UserProfile(household_id=int(h["X-Household-Id"]), display_name="Owner",
                                role="member", is_active=True)
            session.add(owner)
            session.flush()
            account = session.get(Account, aid)
            assert account is not None
            account.owner_profile_id = owner.id
            session.commit()
            owner_headers = {**h, "X-Profile-Id": str(owner.id)}
        batch = self._upload(client, owner_headers, aid)["import_row"]["id"]
        assert client.get("/api/v1/imports", headers=owner_headers).json()["total"] == 1
        assert client.get("/api/v1/imports", headers=h).json()["total"] == 0
        assert client.post(f"/api/v1/imports/{batch}/commit", headers=owner_headers).json()[
            "rows_imported"] == 3

    def test_ofx_bank_ids_survive_commit_and_reimport(self, client: TestClient) -> None:
        h, aid = self._setup(client)
        records = "".join(f"<STMTTRN><DTPOSTED>20250401<TRNAMT>-25.40<NAME>TESCO STORES"
                          f"<FITID>{fitid}</STMTTRN>" for fitid in ("one", "two", "one"))
        def preview() -> dict:
            response = client.post("/api/v1/imports/preview", headers=h,
                                   data={"account_id": str(aid), "fmt": "ofx"},
                                   files={"file": ("statement.ofx", records.encode())})
            assert response.status_code == 201, response.text
            return response.json()

        batch = preview()
        assert [row["duplicate"] for row in batch["rows"]] == [False, False, True]
        url = f"/api/v1/imports/{batch['import_row']['id']}"
        assert client.post(f"{url}/commit", headers=h).json()["rows_imported"] == 2
        assert all(row["duplicate"] for row in preview()["rows"])
        assert self._upload(client, h, aid)["rows"][0]["duplicate"]
        assert client.post(f"{url}/rollback", headers=h).status_code == 200
        retry = preview()
        assert client.post(f"/api/v1/imports/{retry['import_row']['id']}/commit",
                           headers=h).json()["rows_imported"] == 2

    @pytest.mark.parametrize("choice, inserted, status", [
        (1, 1, TransactionStatus.reconciled),
        (1, 1, TransactionStatus.cleared),
        (None, 2, TransactionStatus.reconciled),
    ])
    def test_reconcile_legacy_payment_and_reimport(self, client: TestClient,
                                                 choice: int | None, inserted: int,
                                                 status: TransactionStatus) -> None:
        h, aid = self._setup(client)
        old = self._upload(client, h, aid)["import_row"]["id"]
        assert client.post(f"/api/v1/imports/{old}/commit", headers=h).status_code == 200
        with Session(client.app.state.engine) as session:
            purchase = session.scalars(select(Transaction).where(
                Transaction.statement_import_id == old, Transaction.amount_pence == -2540)).one()
            purchase.status = status
            purchase_id, fingerprint = purchase.id, purchase.external_ref
            session.commit()
        records = "".join(f"<STMTTRN><DTPOSTED>20250401<TRNAMT>-25.40<NAME>TESCO STORES"
                          f"<FITID>{fitid}</STMTTRN>" for fitid in ("first", "second"))

        def preview() -> dict:
            result = client.post("/api/v1/imports/preview", headers=h,
                data={"account_id": str(aid), "fmt": "ofx"},
                files={"file": ("statement.ofx", records.encode())})
            assert result.status_code == 201, result.text
            return result.json()

        staged = preview()
        assert [r["legacy_transaction_id"] for r in staged["rows"]] == [purchase_id, purchase_id]
        url = f"/api/v1/imports/{staged['import_row']['id']}"
        assert client.post(f"{url}/commit", headers=h).status_code == 409
        assert client.post(f"{url}/commit", headers=h,
            json={"legacy_matches": {str(purchase_id): choice}}).json()["rows_imported"] == inserted
        assert all(r["duplicate"] for r in preview()["rows"])
        with Session(client.app.state.engine) as session:
            historic = session.get(Transaction, purchase_id)
            assert historic is not None and historic.external_ref == fingerprint
            assert historic.status is status
        if status is TransactionStatus.cleared and choice is not None:
            assert client.post(f"/api/v1/imports/{old}/rollback", headers=h).status_code == 409
            assert all(r["duplicate"] for r in preview()["rows"])
        assert client.post(f"{url}/rollback", headers=h).status_code == 200
        assert all(r["legacy_transaction_id"] == purchase_id for r in preview()["rows"])

    def test_referenced_batch_stays_intact(self, client: TestClient) -> None:
        h, aid = self._setup(client)
        batch = self._upload(client, h, aid)["import_row"]["id"]
        url = f"/api/v1/imports/{batch}"
        assert client.post(f"{url}/commit", headers=h).status_code == 200
        with Session(client.app.state.engine) as session:
            purchase = session.scalars(select(Transaction).where(
                Transaction.statement_import_id == batch, Transaction.amount_pence < 0)).first()
            assert purchase is not None
            session.add(Transaction(account_id=aid, date=purchase.date,
                                    amount_pence=125, payee="Refund", refund_of_id=purchase.id))
            session.commit()
        assert client.post(f"{url}/rollback", headers=h).status_code == 409
        with Session(client.app.state.engine) as session:
            assert len(list(session.scalars(select(Transaction.id).where(
                Transaction.statement_import_id == batch)))) == 3

    def test_locked_batch_stays_intact(self, client: TestClient) -> None:
        from app.domain import periods

        h, aid = self._setup(client)
        batch = self._upload(client, h, aid)["import_row"]["id"]
        url = f"/api/v1/imports/{batch}"
        assert client.post(f"{url}/commit", headers=h).status_code == 200
        with Session(client.app.state.engine) as session:
            p = periods.open_period(session, int(h["X-Household-Id"]), "Synthetic",
                                    date(2025, 4, 1), date(2025, 4, 30))
            periods.lock_period(session, p)
            session.commit()
        assert client.post(f"{url}/rollback", headers=h).status_code == 409
        assert client.get("/api/v1/imports", headers=h).json()["items"][0]["status"] == "committed"

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
