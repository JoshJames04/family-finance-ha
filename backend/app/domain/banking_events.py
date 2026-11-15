"""Events from persisted banking transitions, never aggregate inference (#522)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import outbox
from app.persistence.models import Account, BankAccountMapping, BankConnection, Transaction


def transaction_changed(session: Session, txn: Transaction, event_type: str, *,
                        dedup_key: str | None = None, status: str | None = None) -> None:
    account = session.get(Account, txn.account_id)
    if account is None:
        return
    outbox.emit(session, account.household_id, event_type,
                entity_kind="transaction", entity_id=txn.id,
                payload={"source": str(txn.source), "status": status or str(txn.status),
                         "date": txn.date.isoformat(), "account_id": account.id,
                         "payee": txn.payee, "amount_pence": txn.amount_pence},
                dedup_key=dedup_key)


def balance_changed(session: Session, mapping: BankAccountMapping,
                    previous: int | None) -> None:
    if (mapping.account_id is None or previous is None
            or previous == mapping.balance_pence):
        return
    outbox.emit(session, mapping.household_id, "account.balance_changed",
                entity_kind="account", entity_id=mapping.account_id,
                payload={"source": "starling", "status": "observed",
                         "old_balance_pence": previous, "balance_pence": mapping.balance_pence})


def connection_changed(session: Session, conn: BankConnection, event_type: str) -> None:
    # Each mapped account gets only its own scope. Never copy provider IDs,
    # connection labels, credentials, error text or another account's identity.
    account_ids = set(session.scalars(select(BankAccountMapping.account_id).where(
        BankAccountMapping.connection_id == conn.id,
        BankAccountMapping.account_id.is_not(None))))
    for account_id in account_ids:
        outbox.emit(session, conn.household_id, event_type,
                    entity_kind="account", entity_id=account_id,
                    payload={"source": str(conn.provider), "status": str(conn.status)})
    if not account_ids:
        outbox.emit(session, conn.household_id, event_type,
                    entity_kind="bank_connection", entity_id=conn.id,
                    payload={"source": str(conn.provider), "status": str(conn.status)})
