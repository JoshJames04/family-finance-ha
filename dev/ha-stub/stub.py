"""Minimal Home Assistant API stub for local development.

Stands in for the Supervisor/HA endpoints the backend will call (ingress
headers, user info, supervisor token checks). Development only — accepts a
fixed token, returns fixed synthetic users.
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="HA stub")

STUB_TOKEN = "dev-supervisor-token"
USERS = {
    "ha-user-1": {"id": "ha-user-1", "name": "Bob Example", "is_owner": True},
    "ha-user-2": {"id": "ha-user-2", "name": "Sam Example", "is_owner": False},
}


def _auth(authorization: str | None) -> None:
    if authorization != f"Bearer {STUB_TOKEN}":
        raise HTTPException(status_code=401, detail="bad supervisor token")


@app.get("/api/")
def api_root(authorization: str | None = Header(default=None)) -> dict[str, str]:
    _auth(authorization)
    return {"message": "API running (stub)"}


@app.get("/api/states/{entity_id}")
def state(entity_id: str, authorization: str | None = Header(default=None)) -> dict:
    _auth(authorization)
    return {"entity_id": entity_id, "state": "42.0",
            "attributes": {"unit_of_measurement": "kWh"}}


@app.get("/api/config")
def config(authorization: str | None = Header(default=None)) -> dict:
    _auth(authorization)
    return {"location_name": "Test Home", "version": "2026.11.0",
            "users": list(USERS.values())}
