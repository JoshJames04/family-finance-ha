"""Sheet extractors — map staged import_records to domain entities.

The framework ships with the Income extractor as the end-to-end proof;
per-sheet extractors for budgets/payroll/loans are issues #46–48
(blocked on the real workbook's confirmed structure).

Every entity created carries source=excel_import, the record's id in
``import_record_id``, and dedup ref ``imp:{batch}:{record}``.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    ImportBatch,
    ImportRecord,
    Transaction,
    TransactionSource,
    TransactionStatus,
)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def record_values(record: ImportRecord) -> dict[int, Any]:
    return {c: v for c, v in json.loads(record.interpreted_value or "[]")}


class IncomeExtractor:
    """Income sheet rows: (Month, Employer, Gross, Net-formula)."""

    sheet = "Income"

    def __init__(self, account_id: int, year: int) -> None:
        self.account_id = account_id
        self.year = year

    def map_record(
        self, session: Session, batch: ImportBatch, record: ImportRecord
    ) -> tuple[str, int] | None:
        vals = record_values(record)
        month = _MONTHS.get(str(vals.get(1, ""))[:3])
        gross = vals.get(3)
        if month is None or not isinstance(gross, (int, float)):
            return None
        ref = f"imp:{batch.id}:{record.id}"
        t = Transaction(
            account_id=self.account_id,
            date=date(self.year, month, 1),
            amount_pence=int(gross),
            payee=str(vals.get(2, "excel import")),
            status=TransactionStatus.cleared,
            source=TransactionSource.excel_import,
            external_ref=ref, import_record_id=record.id,
        )
        session.add(t)
        session.flush()
        return "transaction", t.id


def already_imported(session: Session, batch_id: int,
                     record_id: int) -> int | None:
    return session.execute(
        select(Transaction.id).where(
            Transaction.external_ref == f"imp:{batch_id}:{record_id}")
    ).scalar_one_or_none()
