"""Bank overlap review follows the underlying account's existing access rules."""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import (
    Account,
    Household,
    MergeCandidate,
    MergeStatus,
    Role,
    Transaction,
    TransactionSource,
    TransactionStatus,
    UserProfile,
)


@pytest.fixture()
def review(tmp_path):
    app = create_app(Settings.load({"FF_DATA_DIR": str(tmp_path),
        "FF_SCHEDULER_TICK_SECONDS": "0", "FF_BACKUP_INTERVAL_HOURS": "0"}))
    with TestClient(app) as client, Session(app.state.engine) as session:
        household = Household(name="Synthetic household")
        other_household = Household(name="Other synthetic household")
        session.add_all([household, other_household])
        session.flush()
        profiles = {}
        for name, role in (("owner", Role.member), ("member", Role.member),
                           ("viewer", Role.viewer), ("admin", Role.admin)):
            profile = UserProfile(household_id=household.id, display_name=name, role=role)
            session.add(profile)
            session.flush()
            profiles[name] = profile.id
        candidates = {}
        for name, hid, owner in (("shared", household.id, None),
                                 ("private", household.id, profiles["owner"]),
                                 ("foreign", other_household.id, None)):
            account = Account(household_id=hid, name=name, owner_profile_id=owner,
                              type="personal", opening_date=date(2026, 1, 1),
                              opening_balance_pence=0)
            session.add(account)
            session.flush()
            txn = Transaction(account_id=account.id, date=date(2026, 4, 1),
                              amount_pence=-500, payee=f"Synthetic {name}",
                              source=TransactionSource.manual, status=TransactionStatus.cleared)
            session.add(txn)
            session.flush()
            candidate = MergeCandidate(household_id=hid, account_id=account.id,
                existing_txn_id=txn.id, incoming={"external_ref": f"synthetic-{name}",
                    "date": "2026-04-01", "amount_pence": -500, "payee": f"Synthetic {name}"})
            session.add(candidate)
            session.flush()
            candidates[name] = (candidate.id, txn.id)
        hid = household.id
        session.commit()
        yield client, hid, profiles, candidates


def headers(hid, profiles, actor):
    return {"X-Household-Id": str(hid),
            **({"X-Profile-Id": str(profiles[actor])} if actor else {})}


@pytest.mark.parametrize("actor,expected", [
    (None, {"shared"}), ("member", {"shared"}), ("viewer", {"shared"}),
    ("owner", {"shared", "private"}), ("admin", {"shared", "private"}),
])
def test_review_list_filters_private_and_cross_household_candidates(review, actor, expected):
    client, hid, profiles, candidates = review
    response = client.get("/api/v1/sync/merge-candidates", headers=headers(hid, profiles, actor))
    assert response.status_code == 200
    assert {row["id"] for row in response.json()} == {candidates[name][0] for name in expected}


@pytest.mark.parametrize("actor,target,status", [
    (None, "private", 404), ("member", "private", 404), ("viewer", "private", 404),
    ("viewer", "shared", 403), ("owner", "foreign", 404),
])
def test_unauthorised_resolution_cannot_mutate_candidate_or_transaction(
        review, actor, target, status):
    client, hid, profiles, candidates = review
    candidate_id, txn_id = candidates[target]
    response = client.post(f"/api/v1/sync/merge-candidates/{candidate_id}/resolve",
                           headers=headers(hid, profiles, actor), json={"action": "merged"})
    assert response.status_code == status
    if status == 404:
        assert response.json()["title"] == f"candidate {candidate_id} not found"
    with Session(client.app.state.engine) as persisted:
        assert persisted.get(MergeCandidate, candidate_id).status == MergeStatus.pending
        assert persisted.get(Transaction, txn_id).external_ref is None


@pytest.mark.parametrize("actor,target", [("member", "shared"), ("owner", "private"),
                                          ("admin", "private")])
def test_existing_writable_account_scope_can_resolve_overlap(review, actor, target):
    client, hid, profiles, candidates = review
    candidate_id, txn_id = candidates[target]
    response = client.post(f"/api/v1/sync/merge-candidates/{candidate_id}/resolve",
                           headers=headers(hid, profiles, actor), json={"action": "merged"})
    assert response.status_code == 200
    with Session(client.app.state.engine) as persisted:
        assert persisted.get(MergeCandidate, candidate_id).status == MergeStatus.merged
        assert persisted.get(Transaction, txn_id).external_ref == f"synthetic-{target}"


def test_inconsistent_candidate_cannot_write_a_different_accounts_transaction(review):
    client, hid, profiles, candidates = review
    candidate_id, _ = candidates["shared"]
    private_txn_id = candidates["private"][1]
    with Session(client.app.state.engine) as session:
        session.get(MergeCandidate, candidate_id).existing_txn_id = private_txn_id
        session.commit()
    visible = client.get("/api/v1/sync/merge-candidates", headers=headers(hid, profiles, "member"))
    assert candidate_id not in {row["id"] for row in visible.json()}
    response = client.post(f"/api/v1/sync/merge-candidates/{candidate_id}/resolve",
                           headers=headers(hid, profiles, "member"), json={"action": "merged"})
    assert response.status_code == 404
    with Session(client.app.state.engine) as persisted:
        assert persisted.get(Transaction, private_txn_id).external_ref is None


@pytest.mark.parametrize("target_private", [False, True])
@pytest.mark.parametrize("decision", ["pending", "merged", "dismissed"])
def test_account_merge_keeps_overlap_history_in_the_target_scope(review, target_private, decision):
    client, hid, profiles, candidates = review
    candidate_id, txn_id = candidates["shared" if target_private else "private"]
    own = headers(hid, profiles, "owner")
    if decision != "pending":
        assert client.post(f"/api/v1/sync/merge-candidates/{candidate_id}/resolve", headers=own,
                           json={"action": decision}).status_code == 200
    with Session(client.app.state.engine) as session:
        source_id = session.get(Transaction, txn_id).account_id
        target = Account(household_id=hid, name="Merged target", type="personal",
                         opening_date=date(2026, 1, 1), opening_balance_pence=0,
                         owner_profile_id=profiles["owner"] if target_private else None)
        session.add(target)
        session.flush()
        target_id = target.id
        session.commit()
    merged = client.post(f"/api/v1/accounts/{source_id}/merge-into/{target_id}", headers=own)
    assert merged.status_code == 200
    with Session(client.app.state.engine) as persisted:
        assert persisted.get(Transaction, txn_id).account_id == target_id
        candidate = persisted.get(MergeCandidate, candidate_id)
        assert candidate.account_id == target_id
        assert candidate.status == decision
    if decision == "pending":
        owner_rows = client.get("/api/v1/sync/merge-candidates", headers=own).json()
        assert candidate_id in {row["id"] for row in owner_rows}
        member = headers(hid, profiles, "member")
        member_rows = client.get("/api/v1/sync/merge-candidates", headers=member).json()
        assert (candidate_id in {row["id"] for row in member_rows}) is not target_private
        if target_private:
            assert client.post(f"/api/v1/sync/merge-candidates/{candidate_id}/resolve",
                               headers=member, json={"action": "merged"}).status_code == 404
        assert client.post(f"/api/v1/sync/merge-candidates/{candidate_id}/resolve", headers=own,
                           json={"action": "merged"}).status_code == 200
