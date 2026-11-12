"""Provider aliases require confirmed mapping identity, never guessed names."""

from datetime import date

import pytest

from app.domain import categorise
from app.persistence.models import Account, BankAccountMapping, BankConnection, Transaction


@pytest.mark.parametrize(
    "variant,expected", [("unique", 1), ("ambiguous", 0), ("other_uid", 0), ("other_connection", 0)]
)
def test_linked_provider_aliases_require_same_connection_uid_and_unique_label(
    session, household, variant, expected
):
    accounts = [
        Account(
            household_id=household.id,
            name=name,
            type="personal",
            opening_balance_pence=0,
            opening_date=date(2025, 1, 1),
        )
        for name in ("Main", "Bills", "Duplicate")
    ]
    connection = BankConnection(
        household_id=household.id, provider="starling", vault_ref="synthetic-not-a-secret"
    )
    session.add_all([*accounts, connection])
    session.flush()
    other_connection = BankConnection(
        household_id=household.id, provider="starling", vault_ref="synthetic-other"
    )
    session.add(other_connection)
    session.flush()
    for account, label, uid, category in [
        (accounts[0], "Provider main", "synthetic-uid", "main"),
        (
            accounts[1],
            "Provider bills",
            "other-uid" if variant == "other_uid" else "synthetic-uid",
            "space",
        ),
    ]:
        session.add(
            BankAccountMapping(
                household_id=household.id,
                connection_id=(
                    other_connection.id
                    if variant == "other_connection" and account.id == accounts[1].id
                    else connection.id
                ),
                external_uid=uid,
                external_category_uid=category,
                external_label=label,
                account_id=account.id,
                status="linked",
            )
        )
    if variant == "ambiguous":
        session.add(
            BankAccountMapping(
                household_id=household.id,
                connection_id=connection.id,
                external_uid="synthetic-uid",
                external_category_uid="duplicate",
                external_label="Provider main",
                account_id=accounts[2].id,
                status="linked",
            )
        )
    session.add_all(
        [
            Transaction(
                account_id=accounts[0].id,
                date=date(2025, 4, 10),
                amount_pence=-500,
                payee="Provider bills",
                source="starling",
            ),
            Transaction(
                account_id=accounts[1].id,
                date=date(2025, 4, 10),
                amount_pence=500,
                payee="Provider main",
                source="starling",
            ),
        ]
    )
    session.flush()
    assert categorise.auto_pair_transfers(session, household.id) == expected
