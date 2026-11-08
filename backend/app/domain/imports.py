"""Bank-statement file import (issue #288).

Parse CSV/OFX/QIF into normalized rows, fingerprint each
(date+amount+payee → ``imp:<sha1>`` external_ref) so re-importing the
same file or an overlapping period dedups at the database constraint,
and track each import as an ``ImportBatch`` for audit + rollback.
Everything here is advisory until the member commits a preview.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.persistence.models import (
    StatementImport,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB — a statement, not a ledger
MAX_ROWS = 10_000

_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d %b %Y",
    "%d %B %Y", "%d-%b-%Y", "%Y/%m/%d",
)


class ImportError_(ValueError):
    """Raised for unreadable files or unresolvable mappings."""


@dataclass
class ParsedRow:
    date: date
    amount_pence: int
    payee: str
    notes: str | None = None
    fingerprint: str = field(default="", init=False)
    duplicate: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        fp = hashlib.sha1(
            f"{self.date.isoformat()}|{self.amount_pence}|"
            f"{self.payee.strip().lower()}".encode()).hexdigest()[:16]
        self.fingerprint = f"imp:{fp}"


def _parse_date(raw: str) -> date:
    s = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ImportError_(f"unrecognised date {s!r}")


def _pence(raw: str) -> int:
    s = raw.strip().replace("£", "").replace(",", "")
    if not s:
        raise ImportError_("empty amount")
    try:
        return int((Decimal(s) * 100).quantize(Decimal("1")))
    except InvalidOperation as exc:
        raise ImportError_(f"unrecognised amount {raw!r}") from exc


def parse_csv(content: bytes, mapping: dict[str, object]) -> list[ParsedRow]:
    """Parse a bank CSV using a user-supplied column mapping.

    ``mapping`` keys: ``date``, ``payee``, ``amount`` (single signed
    column) or ``debit``/``credit`` pair (Money Out / Money In style),
    optional ``notes``, optional ``skip_header`` (default true),
    ``delimiter`` (default: sniffed). Columns are header names, or
    integer indexes when the file has no header row."""
    if len(content) > MAX_FILE_BYTES:
        raise ImportError_("file exceeds 2 MB")
    text = content.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    delim = str(mapping.get("delimiter") or dialect.delimiter)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        raise ImportError_("empty file")
    skip = bool(mapping.get("skip_header", True))
    header = rows[0] if skip else None

    def col(key: str) -> int | None:
        v = mapping.get(key)
        if v is None or v == "":
            return None
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
            return int(v)
        if header is None:
            raise ImportError_(
                f"column {v!r} named but file has no header row")
        try:
            return header.index(str(v))
        except ValueError as exc:
            raise ImportError_(f"column {v!r} not in header") from exc

    i_date, i_payee = col("date"), col("payee")
    i_amount, i_debit, i_credit = (
        col("amount"), col("debit"), col("credit"))
    i_notes = col("notes")
    if i_date is None or i_payee is None:
        raise ImportError_("date and payee columns are required")
    if i_amount is None and i_debit is None and i_credit is None:
        raise ImportError_(
            "an amount column or debit/credit pair is required")

    out: list[ParsedRow] = []
    for line_no, row in enumerate(rows[1 if skip else 0:], start=2):
        if not any(c.strip() for c in row):
            continue
        try:
            if i_amount is not None:
                amount = _pence(row[i_amount])
            else:
                debit = (_pence(row[i_debit])
                         if i_debit is not None and row[i_debit].strip()
                         else 0)
                credit = (_pence(row[i_credit])
                          if i_credit is not None and row[i_credit].strip()
                          else 0)
                amount = credit - abs(debit)
            payee = row[i_payee].strip()
            if not payee:
                continue
            out.append(ParsedRow(
                date=_parse_date(row[i_date]),
                amount_pence=amount,
                payee=payee[:200],
                notes=(row[i_notes].strip()[:2000]
                       if i_notes is not None and i_notes < len(row)
                       else None),
            ))
        except IndexError as exc:
            raise ImportError_(
                f"row {line_no} has fewer columns than the header") from exc
    if not out:
        raise ImportError_("no transactions parsed")
    if len(out) > MAX_ROWS:
        raise ImportError_(f"more than {MAX_ROWS} rows")
    return out


_OF_BLOCK = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.S | re.I)
_OF_TAG = re.compile(r"<(\w+)>([^<\r\n]+)", re.I)


def parse_ofx(content: bytes) -> list[ParsedRow]:
    """OFX 1.x (SGML) statement entries — the common UK export."""
    if len(content) > MAX_FILE_BYTES:
        raise ImportError_("file exceeds 2 MB")
    text = content.decode("utf-8", errors="replace")
    out: list[ParsedRow] = []
    for m in _OF_BLOCK.finditer(text):
        tags = {k.upper(): v.strip() for k, v in _OF_TAG.findall(m.group(1))}
        raw_date = tags.get("DTPOSTED", "")[:8]
        if not raw_date or "TRNAMT" not in tags:
            continue
        try:
            d = datetime.strptime(raw_date, "%Y%m%d").date()
            amount = _pence(tags["TRNAMT"])
        except ImportError_:
            continue
        payee = (tags.get("NAME") or tags.get("MEMO") or "OFX entry")
        out.append(ParsedRow(
            date=d, amount_pence=amount, payee=payee[:200],
            notes=(tags.get("MEMO", "")[:2000] or None)))
    if not out:
        raise ImportError_("no statement entries found")
    return out


def parse_qif(content: bytes) -> list[ParsedRow]:
    """Quicken Interchange Format: D<date> T<amount> P<payee> ^ ends."""
    if len(content) > MAX_FILE_BYTES:
        raise ImportError_("file exceeds 2 MB")
    text = content.decode("utf-8", errors="replace")
    out: list[ParsedRow] = []
    cur: dict[str, str] = {}
    for raw in text.splitlines() + ["^"]:
        line = raw.strip()
        if line == "^":
            if cur.get("D") and cur.get("T") and cur.get("P"):
                with contextlib.suppress(ImportError_):
                    out.append(ParsedRow(
                        date=_parse_date(cur["D"]),
                        amount_pence=_pence(cur["T"]),
                        payee=cur["P"][:200],
                        notes=(cur.get("M") or None)))
            cur = {}
        elif line[:1] in "DTPM":
            cur[line[0]] = line[1:]
    if not out:
        raise ImportError_("no transactions found")
    return out


def parse(content: bytes, fmt: str,
          mapping: dict[str, object]) -> list[ParsedRow]:
    if fmt == "ofx":
        return parse_ofx(content)
    if fmt == "qif":
        return parse_qif(content)
    return parse_csv(content, mapping)


def mark_duplicates(session: Session, account_id: int,
                    rows: list[ParsedRow]) -> None:
    """Flag rows already present under their fingerprint."""
    existing = set(session.execute(
        select(Transaction.external_ref).where(
            Transaction.account_id == account_id,
            Transaction.source == TransactionSource.file_import,
            Transaction.external_ref.in_(
                [r.fingerprint for r in rows]))
    ).scalars())
    for r in rows:
        r.duplicate = r.fingerprint in existing


def commit(session: Session, batch: StatementImport) -> tuple[int, int]:
    """Insert non-duplicate preview rows. Returns (inserted, skipped).

    The fingerprint external_ref makes a concurrent duplicate raise
    IntegrityError at flush — callers see it as a skip, so a repeated
    commit stays idempotent."""
    raw_rows = cast(list[dict[str, object]],
                    batch.payload.get("rows") or [])
    rows = [ParsedRow(date=date.fromisoformat(str(r["date"])),
                      amount_pence=int(str(r["amount_pence"] or 0)),
                      payee=str(r["payee"]),
                      notes=cast("str | None", r.get("notes")))
            for r in raw_rows]
    mark_duplicates(session, batch.account_id, rows)
    inserted = skipped = 0
    for r in rows:
        if r.duplicate:
            skipped += 1
            continue
        try:
            with session.begin_nested():
                session.add(Transaction(
                    account_id=batch.account_id, date=r.date,
                    amount_pence=r.amount_pence, payee=r.payee,
                    status=TransactionStatus.pending, notes=r.notes,
                    source=TransactionSource.file_import,
                    external_ref=r.fingerprint,
                    statement_import_id=batch.id))
                session.flush()
        except IntegrityError:
            skipped += 1
        else:
            inserted += 1
    return inserted, skipped


def rollback(session: Session, batch: StatementImport) -> int:
    """Delete the batch's imported rows that are still editable."""
    n = session.execute(delete(Transaction).where(
        Transaction.statement_import_id == batch.id,
        Transaction.status != TransactionStatus.reconciled)
        .returning(Transaction.id)).all()
    return len(n)
