"""Issue #68 — auto-categorisation rules.

ACs: a confirmed categorisation improves future suggestions;
internal-account transfers pair into Transfer objects and never
double-count; rule precedence is priority-then-confirmation.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import categorise
from app.main import create_app
from app.persistence.models import (
    Account,
    CategorisationRule,
    Category,
    Confidence,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    Household,
    MatchType,
    ProviderCategoryMap,
    RecurringCommitment,
    RuleSource,
    Transaction,
    TransactionSource,
    TransactionStatus,
)


@pytest.fixture()
def account(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Current",
                type="personal", opening_balance_pence=0,
                opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


@pytest.fixture()
def cats(session: Session, household: Household) -> dict[str, int]:
    out = {}
    for name in ("Groceries", "Fuel", "Dining"):
        c = Category(household_id=household.id, name=name)
        session.add(c)
        session.flush()
        out[name] = c.id
    return out


def _txn(session: Session, a: Account, payee: str, pence: int,
         on: date = date(2025, 4, 10),
         category_id: int | None = None) -> Transaction:
    t = Transaction(account_id=a.id, date=on, amount_pence=pence,
                    payee=payee, status=TransactionStatus.cleared,
                    source="manual", category_id=category_id)
    session.add(t)
    session.flush()
    return t


def _rule(session: Session, household: Household, pattern: str,
          category_id: int, *, priority: int = 100,
          auto_apply: bool = False,
          confirmed: int = 0,
          match: MatchType = MatchType.contains) -> CategorisationRule:
    r = CategorisationRule(
        household_id=household.id, pattern=pattern, match_type=match,
        category_id=category_id, priority=priority,
        auto_apply=auto_apply, times_confirmed=confirmed,
        source=RuleSource.manual)
    session.add(r)
    session.flush()
    return r


class TestPrecedence:
    def test_lower_priority_wins(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        _rule(session, household, "Tesco", cats["Dining"],
              priority=50)
        _rule(session, household, "Tesco", cats["Groceries"],
              priority=10)
        assert categorise.match_rule(
            session, household.id, "TESCO METRO").category_id \
            == cats["Groceries"]

    def test_confirmed_breaks_ties(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        """Learning earns precedence: at equal priority the rule with
        more confirmations wins."""
        _rule(session, household, "Tesco", cats["Dining"],
              priority=10, confirmed=7)
        _rule(session, household, "Tesco", cats["Groceries"],
              priority=10, confirmed=1)
        assert categorise.match_rule(
            session, household.id, "Tesco").category_id \
            == cats["Dining"]

    def test_exact_beats_nothing_when_lower_priority(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        """Match type doesn't override ordering — a higher-priority
        'contains' still beats a lower-priority 'exact'."""
        _rule(session, household, "Tesco", cats["Groceries"],
              priority=5)
        _rule(session, household, "Tesco Metro", cats["Dining"],
              priority=50, match=MatchType.exact)
        assert categorise.match_rule(
            session, household.id, "Tesco Metro").category_id \
            == cats["Groceries"]


class TestApply:
    def test_suggest_vs_auto_apply(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        _rule(session, household, "Tesco", cats["Groceries"],
              auto_apply=True)
        _rule(session, household, "Shell", cats["Fuel"],
              auto_apply=False)
        t1 = _txn(session, account, "TESCO EXTRA", -3_000)
        t2 = _txn(session, account, "SHELL FUEL", -4_000)
        t3 = _txn(session, account, "UNKNOWN SHOP", -500)
        r = categorise.apply_rules(session, household.id)
        assert (r.auto_applied, r.suggested, r.unmatched) == (1, 1, 1)
        assert t1.category_id == cats["Groceries"]
        assert t1.suggested_category_id is None
        # Suggestion is a flag — never a silent categorisation.
        assert t2.category_id is None
        assert t2.suggested_category_id == cats["Fuel"]
        assert t3.category_id is None
        assert t3.suggested_category_id is None


class TestLearning:
    def test_confirm_improves_future(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        """AC: a confirmed categorisation improves future
        suggestions."""
        categorise.learn(session, household.id, "Tesco",
                         cats["Groceries"])
        _txn(session, account, "Tesco", -2_000)
        r = categorise.apply_rules(session, household.id)
        assert r.suggested == 1
        # And the learned rule strengthens on every confirmation.
        rule = categorise.learn(session, household.id, "Tesco",
                                cats["Groceries"])
        assert rule.times_confirmed == 2
        assert rule.source is RuleSource.learned


class TestAutoPair:
    def test_internal_legs_pair_into_transfer(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        """AC: internal-account movements match into transfer
        objects."""
        other = Account(household_id=household.id, name="Savings",
                        type="personal", opening_balance_pence=0,
                        opening_date=date(2025, 1, 1))
        session.add(other)
        session.flush()
        d = _txn(session, account, "Transfer", -20_000)
        c = _txn(session, other, "Transfer", 20_000)
        assert categorise.auto_pair_transfers(
            session, household.id) == 1
        assert d.transfer_id is not None
        assert d.transfer_id == c.transfer_id
        # Re-run: already-linked legs never re-pair.
        assert categorise.auto_pair_transfers(
            session, household.id) == 0

    def test_same_account_never_pairs(
            self, session: Session, household: Household,
            account: Account) -> None:
        _txn(session, account, "adj", -5_000)
        _txn(session, account, "adj", 5_000)
        assert categorise.auto_pair_transfers(
            session, household.id) == 0


class TestAutoRefund:
    def test_links_to_earlier_debit(
            self, session: Session, household: Household,
            account: Account) -> None:
        _txn(session, account, "Amazon", -4_000,
             on=date(2025, 4, 1))
        refund = _txn(session, account, "Amazon", 1_500,
                      on=date(2025, 4, 8))
        assert categorise.auto_link_refunds(
            session, household.id) == 1
        assert refund.refund_of_id is not None

    def test_no_matching_payee(
            self, session: Session, household: Household,
            account: Account) -> None:
        _txn(session, account, "Refund", 1_500)
        assert categorise.auto_link_refunds(
            session, household.id) == 0


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_rule_apply_and_learn(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", json={
            "name": "Current", "type": "personal",
            "opening_balance": 100_000,
            "opening_date": "2025-01-01"}, headers=h).json()["id"]
        cid = client.post("/api/v1/categories",
                          json={"name": "Groceries"},
                          headers=h).json()["id"]
        client.post("/api/v1/categorisation/rules", json={
            "pattern": "Tesco", "category_id": cid}, headers=h)
        tid = client.post("/api/v1/transactions", json={
            "account_id": aid, "date": "2025-04-10",
            "amount": -3_000, "payee": "TESCO EXTRA"},
            headers=h).json()["id"]
        r = client.post("/api/v1/categorisation/apply", headers=h)
        assert r.json()["suggested"] == 1
        # Confirm via PATCH — the learn hook mints a learned rule.
        client.patch(f"/api/v1/transactions/{tid}",
                     json={"category_id": cid}, headers=h)
        rules = client.get("/api/v1/categorisation/rules",
                           headers=h).json()
        learned = [r for r in rules if r["source"] == "learned"]
        assert learned and learned[0]["times_confirmed"] >= 1


def _movement(session: Session, household: Household, a: Account, *,
              on: date, pence: int = -1599,
              source: ExpectedSource = ExpectedSource.commitment,
              source_id: int = 1) -> ExpectedMovement:
    m = ExpectedMovement(
        household_id=household.id, account_id=a.id,
        expected_date=on, amount_pence=pence, label="mv",
        confidence=Confidence.scheduled,
        source_type=source, source_id=source_id)
    session.add(m)
    session.flush()
    return m


class TestMatchInherit:
    """Issue #452 — a matched tx inherits its source's category
    (issue #453: and teaches the rules engine the association)."""

    def _commitment(self, session: Session, household: Household,
                    a: Account, category_id: int | None
                    ) -> RecurringCommitment:
        c = RecurringCommitment(
            household_id=household.id, name="Welsh Water",
            account_id=a.id, amount_pence=-4_500,
            frequency=Frequency.monthly,
            next_date=date(2025, 4, 11), category_id=category_id)
        session.add(c)
        session.flush()
        return c

    def _match(self, session: Session, mv: ExpectedMovement,
               tx: Transaction) -> None:
        session.add(ExpectedMatch(
            movement_id=mv.id, transaction_id=tx.id,
            matched_pence=abs(mv.amount_pence), manual=False))
        session.flush()

    def test_match_inherits_and_learns(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        c = self._commitment(session, household, account,
                             cats["Groceries"])
        mv = _movement(session, household, account,
                       on=date(2025, 4, 11), source_id=c.id)
        tx = _txn(session, account, "DWR CYMRU", -4_500)
        self._match(session, mv, tx)
        n = categorise.inherit_match_categories(session, household.id)
        assert n == 1
        assert tx.category_id == cats["Groceries"]
        rule = session.scalar(
            select(CategorisationRule).where(
                CategorisationRule.pattern == "DWR CYMRU"))
        assert rule is not None
        assert rule.source is RuleSource.learned
        assert rule.category_id == cats["Groceries"]

    def test_user_category_preserved(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        c = self._commitment(session, household, account,
                             cats["Groceries"])
        mv = _movement(session, household, account,
                       on=date(2025, 4, 11), source_id=c.id)
        tx = _txn(session, account, "DWR CYMRU", -4_500,
                  category_id=cats["Fuel"])
        self._match(session, mv, tx)
        n = categorise.inherit_match_categories(session, household.id)
        assert n == 0
        assert tx.category_id == cats["Fuel"]

    def test_source_without_category_skips(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        c = self._commitment(session, household, account, None)
        mv = _movement(session, household, account,
                       on=date(2025, 4, 11), source_id=c.id)
        tx = _txn(session, account, "DWR CYMRU", -4_500)
        self._match(session, mv, tx)
        n = categorise.inherit_match_categories(session, household.id)
        assert n == 0 and tx.category_id is None

    def test_idempotent(self, session: Session, household: Household,
                        account: Account, cats: dict[str, int]) -> None:
        c = self._commitment(session, household, account,
                             cats["Groceries"])
        mv = _movement(session, household, account,
                       on=date(2025, 4, 11), source_id=c.id)
        tx = _txn(session, account, "DWR CYMRU", -4_500)
        self._match(session, mv, tx)
        categorise.inherit_match_categories(session, household.id)
        assert categorise.inherit_match_categories(
            session, household.id) == 0


class TestProviderMap:
    """Issue #453 — provider-enum → household category map."""

    def _feed_txn(self, session: Session, a: Account, *,
                  provider_cat: str | None = "GROCERIES"
                  ) -> Transaction:
        t = Transaction(
            account_id=a.id, date=date(2025, 4, 10),
            amount_pence=-3_000, payee="TESCO",
            status=TransactionStatus.cleared,
            source=TransactionSource.starling,
            provider_category=provider_cat)
        session.add(t)
        session.flush()
        return t

    def test_map_applies(self, session: Session,
                         household: Household, account: Account,
                         cats: dict[str, int]) -> None:
        session.add(ProviderCategoryMap(
            household_id=household.id, provider="starling",
            provider_category="GROCERIES",
            category_id=cats["Groceries"]))
        session.flush()
        tx = self._feed_txn(session, account)
        n = categorise.apply_provider_map(session, household.id)
        assert n == 1 and tx.category_id == cats["Groceries"]

    def test_unmapped_name_match_suggests(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        tx = self._feed_txn(session, account, provider_cat="GROCERIES")
        n = categorise.apply_provider_map(session, household.id)
        assert n == 0
        assert tx.category_id is None
        assert tx.suggested_category_id == cats["Groceries"]

    def test_unmapped_no_name_match_leaves(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        tx = self._feed_txn(session, account, provider_cat="SPACE")
        n = categorise.apply_provider_map(session, household.id)
        assert n == 0
        assert tx.category_id is None
        assert tx.suggested_category_id is None

    def test_no_provider_category_noop(
            self, session: Session, household: Household,
            account: Account, cats: dict[str, int]) -> None:
        tx = self._feed_txn(session, account, provider_cat=None)
        n = categorise.apply_provider_map(session, household.id)
        assert n == 0 and tx.category_id is None


class TestProviderCategoriesApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_observed_and_put(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        client.post("/api/v1/accounts", json={
            "name": "Current", "type": "personal",
            "opening_balance": 100_000,
            "opening_date": "2025-01-01"}, headers=h)
        cid = client.post("/api/v1/categories",
                          json={"name": "Groceries"},
                          headers=h).json()["id"]
        r = client.put(
            "/api/v1/categorisation/provider-categories",
            json=[{"provider": "starling",
                   "provider_category": "GROCERIES",
                   "category_id": cid}], headers=h)
        assert r.status_code == 200
        assert r.json()[0]["provider_category"] == "GROCERIES"
        rows = client.get(
            "/api/v1/categorisation/provider-categories",
            headers=h).json()
        assert rows == []

    def test_put_validates_category(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        r = client.put(
            "/api/v1/categorisation/provider-categories",
            json=[{"provider": "starling",
                   "provider_category": "GROCERIES",
                   "category_id": 9999}], headers=h)
        assert r.status_code == 422

