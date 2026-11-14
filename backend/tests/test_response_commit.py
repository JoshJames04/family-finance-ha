"""Successful HTTP responses must describe committed database writes."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.api.v1.households import router
from app.persistence import session as persistence
from app.persistence.models import Household


def test_household_is_committed_before_success_headers(session, monkeypatch):
    factory = sessionmaker(bind=session.get_bind())
    monkeypatch.setattr(persistence, "_session_factory", factory)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    visible_at_headers = []

    async def observed(scope, receive, send):
        async def capture(message):
            if message["type"] == "http.response.start":
                with factory() as independent:
                    visible_at_headers.append(independent.scalar(
                        select(Household.id).where(Household.name == "Committed response")))
            await send(message)
        await app(scope, receive, capture)

    with TestClient(observed) as client:
        response = client.post("/api/v1/households", json={"name": "Committed response"})
    assert response.status_code == 201
    assert visible_at_headers == [response.json()["id"]]


def test_commit_failure_returns_error_instead_of_success(session, monkeypatch):
    factory = sessionmaker(bind=session.get_bind())
    monkeypatch.setattr(persistence, "_session_factory", factory)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @event.listens_for(factory, "before_commit")
    def fail_commit(_session):
        raise IntegrityError("synthetic commit failure", {}, Exception("rejected"))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/households", json={"name": "Rejected response"})
    assert response.status_code == 500
    with sessionmaker(bind=session.get_bind())() as independent:
        assert independent.scalar(select(Household.id).where(
            Household.name == "Rejected response")) is None
