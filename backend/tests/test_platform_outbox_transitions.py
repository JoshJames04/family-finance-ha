"""Canonical transitions emit transactionally; polling never invents events."""
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain import budgets
from app.main import create_app
from app.persistence.models import BudgetLineSide, BudgetPeriodType, Category, IntegrationEvent
from app.persistence.session import get_session


def test_budget_approval_emits_canonical_transition(session, household):
    cat = Category(household_id=household.id, name="Synthetic")
    session.add(cat)
    session.flush()
    budget = budgets.create_budget(session, household.id, "Synthetic", BudgetPeriodType.monthly,
                                   date(2026, 1, 1), date(2026, 1, 31))
    budgets.add_line(session, budget, cat.id, BudgetLineSide.expense, 100)
    budgets.approve(session, budget)
    events = session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "budget.approved")).all()
    assert len(events) == 1
    assert events[0].entity_id == budget.id
    assert events[0].payload == {"status": "approved"}


def test_energy_ingest_repeat_does_not_repeat_transition(session, household):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)
    body = {"date": "2026-01-02", "wh": 1234, "source": "ha"}
    assert client.post("/api/v1/energy/readings", json=body).status_code == 201
    assert client.post("/api/v1/energy/readings", json=body).status_code == 201
    events = session.scalars(select(IntegrationEvent).where(
        IntegrationEvent.event_type == "energy.reading_updated")).all()
    assert len(events) == 1
    assert events[0].payload == {"date": "2026-01-02", "source": "ha"}
