"""Issue #96 — seasonal events + gifts: generic event type (Christmas
first, birthdays later with NO schema change), gift lifecycle
transitions, payment flow, and budget-vs-committed totals."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.domain import seasonal
from app.domain.seasonal import SeasonalError
from app.main import create_app
from app.persistence.models import (
    Account,
    Gift,
    Household,
    PaymentStatus,
    PurchaseStatus,
    SeasonalEvent,
    Transaction,
    TransactionStatus,
)


@pytest.fixture()
def event(session: Session, household: Household) -> SeasonalEvent:
    e = SeasonalEvent(household_id=household.id, year=2026,
                      type="christmas", name="Christmas 2026",
                      budget_pence=100_000)
    session.add(e)
    session.flush()
    return e


def _gift(session: Session, e: SeasonalEvent, planned: int = 5_000,
          status: PurchaseStatus = PurchaseStatus.idea) -> Gift:
    g = Gift(event_id=e.id, recipient="Mum", idea="Scarf",
             planned_pence=planned, purchase_status=status)
    session.add(g)
    session.flush()
    return g


class TestEventTypes:
    def test_second_type_needs_no_schema_change(
            self, session: Session, household: Household,
            event: SeasonalEvent) -> None:
        """AC: 'birthday' works through the same table — the type
        column is free text by design."""
        b = SeasonalEvent(household_id=household.id, year=2026,
                          type="birthday", name="Mum's 60th",
                          budget_pence=20_000)
        session.add(b)
        session.flush()
        assert b.id != event.id
        assert b.type == "birthday"

    def test_same_type_same_year_rejected(
            self, session: Session, household: Household,
            event: SeasonalEvent) -> None:
        dup = SeasonalEvent(household_id=household.id, year=2026,
                            type="christmas", name="Again",
                            budget_pence=1)
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()


class TestTransitions:
    def test_happy_path_full_lifecycle(self, session: Session,
                                       event: SeasonalEvent) -> None:
        g = _gift(session, event)
        for to in (PurchaseStatus.planned, PurchaseStatus.purchased,
                   PurchaseStatus.wrapped, PurchaseStatus.given):
            seasonal.transition_purchase(g, to)
        assert g.purchase_status is PurchaseStatus.given

    def test_skip_step_rejected(self, session: Session,
                              event: SeasonalEvent) -> None:
        g = _gift(session, event)
        with pytest.raises(SeasonalError):
            seasonal.transition_purchase(g, PurchaseStatus.purchased)

    def test_backwards_rejected(self, session: Session,
                              event: SeasonalEvent) -> None:
        g = _gift(session, event, status=PurchaseStatus.purchased)
        with pytest.raises(SeasonalError):
            seasonal.transition_purchase(g, PurchaseStatus.idea)

    def test_terminal_state_rejected(self, session: Session,
                                     event: SeasonalEvent) -> None:
        g = _gift(session, event, status=PurchaseStatus.given)
        with pytest.raises(SeasonalError):
            seasonal.transition_purchase(g, PurchaseStatus.wrapped)


class TestPayment:
    def test_pay_requires_purchase(self, session: Session,
                                   event: SeasonalEvent) -> None:
        g = _gift(session, event, status=PurchaseStatus.planned)
        g.actual_pence = 4_500
        with pytest.raises(SeasonalError):
            seasonal.mark_paid(session, g)

    def test_pay_requires_actual(self, session: Session,
                                 event: SeasonalEvent) -> None:
        g = _gift(session, event, status=PurchaseStatus.purchased)
        with pytest.raises(SeasonalError):
            seasonal.mark_paid(session, g)

    def test_pay_links_transaction(self, session: Session,
                                   household: Household,
                                   event: SeasonalEvent) -> None:
        a = Account(household_id=household.id, name="Joint",
                    type="joint", opening_date=date(2026, 1, 1))
        session.add(a)
        session.flush()
        tx = Transaction(account_id=a.id, date=date(2026, 12, 1),
                         amount_pence=-4_500, payee="John Lewis",
                         status=TransactionStatus.cleared,
                         source="manual")
        session.add(tx)
        g = _gift(session, event, status=PurchaseStatus.purchased)
        g.actual_pence = 4_500
        session.flush()
        seasonal.mark_paid(session, g, tx)
        assert g.payment_status is PaymentStatus.paid
        assert g.transaction_id == tx.id


class TestTotals:
    def test_committed_uses_better_known_figure(
            self, session: Session, event: SeasonalEvent) -> None:
        """planned 5000 + purchased gift with actual 7000 + unpaid →
        committed 12000, remaining 88000 of the 100000 budget."""
        _gift(session, event, planned=5_000)
        g2 = _gift(session, event, planned=6_000,
                   status=PurchaseStatus.purchased)
        g2.actual_pence = 7_000
        session.flush()
        t = seasonal.totals(session, event)
        assert t.planned_pence == 11_000
        assert t.actual_pence == 7_000
        assert t.committed_pence == 12_000
        assert t.remaining_pence == 88_000


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> Iterator[TestClient]:
        settings = Settings.load({"FF_DATA_DIR": str(tmp_path)})
        with TestClient(create_app(settings)) as c:
            yield c

    def test_event_gift_flow(self, client: TestClient) -> None:
        hid = client.post("/api/v1/households",
                          json={"name": "H"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        ev = client.post("/api/v1/seasonal-events", headers=h, json={
            "year": 2026, "type": "christmas", "name": "Christmas",
            "budget": 100000})
        assert ev.status_code == 201, ev.json()
        eid = ev.json()["id"]
        g = client.post(f"/api/v1/seasonal-events/{eid}/gifts",
                        headers=h, json={
                            "recipient": "Mum", "idea": "Scarf",
                            "planned": 5000})
        assert g.status_code == 201
        gid = g.json()["id"]
        # invalid skip → 409 problem-details
        r = client.post(
            f"/api/v1/seasonal-events/{eid}/gifts/{gid}/transition",
            headers=h, json={"to": "purchased"})
        assert r.status_code == 409
        assert r.json()["type"] == "about:blank"
        # valid chain + pay
        for to in ("planned", "purchased"):
            r = client.post(
                f"/api/v1/seasonal-events/{eid}/gifts/{gid}/transition",
                headers=h, json={"to": to})
            assert r.status_code == 200
        r = client.post(
            f"/api/v1/seasonal-events/{eid}/gifts/{gid}/pay",
            headers=h, json={"actual": 4500})
        assert r.status_code == 200
        assert r.json()["payment_status"] == "paid"
        t = client.get(f"/api/v1/seasonal-events/{eid}/totals",
                       headers=h).json()
        assert t["committed_pence"] == 4_500
        assert t["remaining_pence"] == 95_500
