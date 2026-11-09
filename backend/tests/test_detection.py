"""Issue #106 — recurring-payment detection: same-payee clusters at a
regular interval become candidates; stable amounts classify as
`subscription`, variable amounts as `variable_dd` (never confirmable
as fixed); dismissed signatures suppress identical re-proposals."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import detection
from app.domain.detection import DetectionError
from app.main import create_app
from app.persistence.models import (
    Account,
    CandidateStatus,
    CandidateType,
    DetectionCandidate,
    Frequency,
    Household,
    RecurringCommitment,
    SubscriptionStatus,
    Transaction,
    TransactionStatus,
    Transfer,
)


@pytest.fixture()
def acct(session: Session, household: Household) -> Account:
    a = Account(household_id=household.id, name="Joint",
                type="joint", opening_date=date(2025, 1, 1))
    session.add(a)
    session.flush()
    return a


def _pay(session: Session, a: Account, payee: str,
         dates: list[date], amounts: list[int],
         source: str = "manual") -> None:
    for d, amt in zip(dates, amounts, strict=True):
        session.add(Transaction(
            account_id=a.id, date=d, amount_pence=-abs(amt),
            payee=payee, status=TransactionStatus.cleared,
            source=source))
    session.flush()


def _monthly(start: date, n: int) -> list[date]:
    """Same day-of-month each month (start day must be <= 28)."""
    out = []
    for i in range(n):
        m = (start.month - 1 + i) % 12 + 1
        y = start.year + (start.month - 1 + i) // 12
        out.append(date(y, m, start.day))
    return out


MONTHS = _monthly(date(2025, 9, 15), 6)


class TestClassification:
    def test_stable_monthly_is_subscription(
            self, session: Session, household: Household,
            acct: Account) -> None:
        _pay(session, acct, "Streamflix", MONTHS,
             [1_099] * 6)
        found = detection.scan(session, household.id)
        assert len(found) == 1
        c = found[0]
        assert c.candidate_type is CandidateType.subscription
        assert c.frequency is Frequency.monthly
        assert c.expected_pence == 1_099
        assert c.confidence >= 80
        assert c.occurrence_count == 6

    def test_variable_amount_is_variable_dd_never_subscription(
            self, session: Session, household: Household,
            acct: Account) -> None:
        # council-tax-like: regular but the amount jumps around
        _pay(session, acct, "Energy Co", MONTHS,
             [9_500, 14_200, 8_800, 16_100, 11_400, 13_000])
        found = detection.scan(session, household.id)
        assert len(found) == 1
        assert found[0].candidate_type is CandidateType.variable_dd

    def test_weekly_and_annual_frequencies(
            self, session: Session, household: Household,
            acct: Account) -> None:
        _pay(session, acct, "CoffeePass",
             [date(2025, 10, 1) + timedelta(days=7 * i)
              for i in range(5)],
             [450] * 5)
        _pay(session, acct, "InsureAll",
             [date(2022, 3, 1), date(2023, 3, 1), date(2024, 3, 1),
              date(2025, 3, 1)],
             [24_000, 24_000, 24_500, 24_000])
        found = {c.payee_normalised: c
                 for c in detection.scan(session, household.id)}
        assert found["coffeepass"].frequency is Frequency.weekly
        assert found["insureall"].frequency is Frequency.annually

    def test_irregular_intervals_rejected(
            self, session: Session, household: Household,
            acct: Account) -> None:
        _pay(session, acct, "RandomShop",
             [date(2025, 9, 1), date(2025, 9, 3), date(2025, 10, 20),
              date(2026, 1, 5)],
             [1_000] * 4)
        assert detection.scan(session, household.id) == []

    def test_too_few_occurrences_rejected(
            self, session: Session, household: Household,
            acct: Account) -> None:
        _pay(session, acct, "NewSub", MONTHS[:2], [999] * 2)
        assert detection.scan(session, household.id) == []

    def test_income_transfers_and_void_ignored(
            self, session: Session, household: Household,
            acct: Account) -> None:
        t = Transfer(household_id=household.id)
        session.add(t)
        session.flush()
        for d in MONTHS:
            # income (positive) — never a subscription candidate
            session.add(Transaction(
                account_id=acct.id, date=d, amount_pence=50_000,
                payee="Employer", status=TransactionStatus.cleared))
            # voided outflow — excluded
            session.add(Transaction(
                account_id=acct.id, date=d, amount_pence=-500,
                payee="Voided", status=TransactionStatus.void))
            # transfer leg — excluded from external cash-flow
            session.add(Transaction(
                account_id=acct.id, date=d, amount_pence=-500,
                payee="Move", transfer_id=t.id,
                status=TransactionStatus.cleared))
        session.flush()
        assert detection.scan(session, household.id) == []


class TestLifecycle:
    def _seed(self, session: Session, acct: Account) -> None:
        _pay(session, acct, "Streamflix", MONTHS, [1_099] * 6)

    def test_confirm_creates_detected_subscription_with_prices(
            self, session: Session, household: Household,
            acct: Account) -> None:
        self._seed(session, acct)
        c = detection.scan(session, household.id)[0]
        sub = detection.confirm(session, c)
        assert sub.source == "detected"
        assert sub.payee == "Streamflix"
        assert sub.frequency is Frequency.monthly
        assert sub.next_renewal_date == date(2026, 3, 15)
        assert len(sub.prices) == 6
        assert all(p.transaction_id is not None for p in sub.prices)
        assert c.status is CandidateStatus.confirmed
        assert c.subscription_id == sub.id

    def test_variable_dd_cannot_be_confirmed(
            self, session: Session, household: Household,
            acct: Account) -> None:
        _pay(session, acct, "Energy Co", MONTHS,
             [9_500, 14_200, 8_800, 16_100, 11_400, 13_000])
        c = detection.scan(session, household.id)[0]
        with pytest.raises(DetectionError):
            detection.confirm(session, c)

    def test_double_confirm_rejected(
            self, session: Session, household: Household,
            acct: Account) -> None:
        self._seed(session, acct)
        c = detection.scan(session, household.id)[0]
        detection.confirm(session, c)
        with pytest.raises(DetectionError):
            detection.confirm(session, c)

    def test_dismissed_not_reproposed_identically(
            self, session: Session, household: Household,
            acct: Account) -> None:
        self._seed(session, acct)
        c = detection.scan(session, household.id)[0]
        detection.dismiss(c, "not a subscription")
        # identical rescan → suppressed
        assert detection.scan(session, household.id) == []
        visible = detection.candidates(session, household.id)
        assert all(x.status is not CandidateStatus.pending
                   for x in visible)

    def test_materially_different_amount_resurfaces(
            self, session: Session, household: Household,
            acct: Account) -> None:
        self._seed(session, acct)
        c = detection.scan(session, household.id)[0]
        detection.dismiss(c)
        # price doubled → not an identical proposal
        _pay(session, acct, "Streamflix",
             _monthly(date(2026, 3, 15), 3), [2_200] * 3)
        found = detection.scan(session, household.id)
        assert len(found) == 1
        assert found[0].expected_pence != 1_099

    def test_rescan_updates_pending_not_duplicates(
            self, session: Session, household: Household,
            acct: Account) -> None:
        self._seed(session, acct)
        first = detection.scan(session, household.id)[0]
        _pay(session, acct, "Streamflix",
             [date(2026, 3, 15)], [1_099])
        again = detection.scan(session, household.id)
        assert len(again) == 1 and again[0].id == first.id
        assert again[0].occurrence_count == 7
        rows = session.query(DetectionCandidate).all()
        assert len(rows) == 1

    def test_registered_payee_skipped(
            self, session: Session, household: Household,
            acct: Account) -> None:
        self._seed(session, acct)
        c = detection.scan(session, household.id)[0]
        detection.confirm(session, c)
        _pay(session, acct, "Streamflix",
             [date(2026, 3, 15), date(2026, 4, 15)], [1_099] * 2)
        assert detection.scan(session, household.id) == []

    def test_commitment_covered_payee_skipped(
            self, session: Session, household: Household,
            acct: Account) -> None:
        """Issue #313 — a payee covered by an active bill never
        surfaces as a subscription candidate."""
        session.add(RecurringCommitment(
            household_id=household.id, name="Streamflix",
            account_id=acct.id, amount_pence=-1_099,
            frequency=Frequency.monthly, day_of_month=15,
            next_date=date(2026, 3, 15), generated_count=0))
        self._seed(session, acct)
        assert detection.scan(session, household.id) == []


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        with TestClient(create_app(
                Settings.load({"FF_DATA_DIR": str(tmp_path)}))) as c:
            yield c

    def test_detect_confirm_dismiss_flow(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = client.post("/api/v1/accounts", headers=h, json={
            "name": "Joint", "type": "joint",
            "opening_balance": 0,
            "opening_date": "2025-01-01"}).json()["id"]
        for d in MONTHS:
            r = client.post("/api/v1/transactions", headers=h, json={
                "account_id": aid, "date": d.isoformat(),
                "amount": -1099, "payee": "Streamflix"})
            assert r.status_code == 201, r.json()

        r = client.post("/api/v1/subscriptions/detect",
                        headers=h, json={})
        assert r.status_code == 200, r.json()
        cands = r.json()
        assert len(cands) == 1
        assert cands[0]["candidate_type"] == "subscription"
        cid = cands[0]["id"]

        listed = client.get("/api/v1/subscriptions/candidates",
                            headers=h).json()
        assert listed[0]["id"] == cid

        r = client.post(
            f"/api/v1/subscriptions/candidates/{cid}/confirm",
            headers=h, json={})
        assert r.status_code == 201, r.json()
        sub = r.json()
        assert sub["source"] == "detected"
        assert sub["status"] == SubscriptionStatus.active

        # second candidate → dismiss hides it from default list
        for d in MONTHS:
            client.post("/api/v1/transactions", headers=h, json={
                "account_id": aid, "date": d.isoformat(),
                "amount": -599, "payee": "MusicBox"})
        cands = client.post("/api/v1/subscriptions/detect",
                            headers=h, json={}).json()
        other = [c for c in cands
                 if c["payee"] == "MusicBox"][0]
        r = client.post(
            f"/api/v1/subscriptions/candidates/{other['id']}/dismiss",
            headers=h, json={"reason": "shared account"})
        assert r.status_code == 200
        assert r.json()["status"] == "dismissed"
        remaining = client.get("/api/v1/subscriptions/candidates",
                               headers=h).json()
        assert all(c["status"] != "dismissed" for c in remaining)

    def test_confirm_unknown_candidate_404(
            self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        r = client.post(
            "/api/v1/subscriptions/candidates/999/confirm",
            headers=h, json={})
        assert r.status_code == 404
