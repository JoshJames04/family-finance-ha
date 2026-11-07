"""Issue #135 — API contract suite.

Pins the surface the frontend + HA integration rely on:
- every FastAPI route is documented in the generated spec (and
  vice-versa) — undocumented endpoints fail CI;
- error responses are uniformly RFC 9457 problem-details;
- real responses validate against the declared schemas;
- a schemathesis sweep proves no path/method returns a 500.
"""
from __future__ import annotations

import tempfile
from typing import Any

import jsonschema
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings

from app.core.settings import Settings
from app.main import create_app

_tmpdir = tempfile.mkdtemp(prefix="ff-contract-")
_app = create_app(Settings.load({"FF_DATA_DIR": _tmpdir}))
# Holding __enter__ (never exiting) runs lifespan once for the session —
# migrations + seeding complete before schemathesis cases fire.
_client = TestClient(_app)
_client.__enter__()

ADMIN = {"X-Admin-Token": "s3cret"}


# ---------- spec ↔ implementation parity -------------------------

def _fresh_app() -> Any:
    """Per-test app — the module-level ``_app`` is built during
    collection and a circular import edge can freeze a partially-built
    router into it. A fresh build sees the completed router."""
    d = tempfile.mkdtemp(prefix="ff-contract-app-")
    return create_app(Settings.load({"FF_DATA_DIR": d}))


def _ops(app: Any) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """(spec ops, route ops) — both derived from the SAME route list:
    the spec is generated fresh via ``get_openapi(routes=app.routes)``
    rather than the ``openapi_schema`` cache, so the two sides can
    never diverge on stale state."""
    from fastapi.openapi.utils import get_openapi

    spec = get_openapi(title="t", version="0", routes=app.routes)
    spec_ops = {(m.upper(), p) for p, ms in spec["paths"].items()
                for m in ms}
    # FastAPI ≥0.130 defers include_router expansion: app.routes holds
    # lazy `_IncludedRouter` objects whose effective contexts carry
    # path/methods — expand recursively, staying version-agnostic.
    route_ops: set[tuple[str, str]] = set()
    stack = list(app.routes)
    while stack:
        r = stack.pop()
        if not getattr(r, "include_in_schema", True):
            continue
        if getattr(r, "methods", None) and getattr(r, "path", None):
            route_ops.update((m, r.path) for m in r.methods)
            continue
        stack.extend(getattr(r, "routes", []) or [])
        eff = getattr(r, "effective_route_contexts", None)
        if callable(eff):
            stack.extend(eff())
    return spec_ops, route_ops


class TestSpecCoverage:
    def test_every_route_is_documented(self) -> None:
        spec_ops, route_ops = _ops(_fresh_app())
        missing = route_ops - spec_ops
        assert not missing, (
            f"undocumented endpoints: {missing} "
            f"(routes={len(route_ops)})")

    def test_no_phantom_spec_paths(self) -> None:
        spec_ops, route_ops = _ops(_fresh_app())
        phantom = spec_ops - route_ops
        assert not phantom, (
            f"spec paths with no route: {phantom} "
            f"(spec={len(spec_ops)}, routes={len(route_ops)})")


# ---------- uniform problem-details ------------------------------

def _problem(body: Any, status: int) -> None:
    assert body["status"] == status
    assert body["type"] == "about:blank"
    assert isinstance(body["title"], str) and body["title"]


class TestErrorUniformity:
    def test_404_unknown_route(self) -> None:
        r = _client.get("/api/v1/definitely-not-here")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith(
            "application/problem+json")
        _problem(r.json(), 404)

    def test_404_unknown_resource(self) -> None:
        r = _client.get("/api/v1/accounts/999999",
                        headers={"X-Household-Id": "1"})
        assert r.status_code == 404
        _problem(r.json(), 404)

    def test_422_missing_field(self) -> None:
        r = _client.post("/api/v1/households", json={})
        assert r.status_code == 422
        _problem(r.json(), 422)

    def test_422_wrong_type(self) -> None:
        r = _client.post("/api/v1/households", json={"name": 123})
        assert r.status_code == 422
        _problem(r.json(), 422)

    def test_403_admin_gate(self) -> None:
        assert _client.get("/api/v1/admin/backups").status_code == 403
        assert _client.get(
            "/api/v1/admin/backups",
            headers={"X-Admin-Token": "wrong"}).status_code == 403

    def test_409_domain_conflict(self) -> None:
        hid = _client.post("/api/v1/households",
                           json={"name": "Contract"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = _client.post("/api/v1/accounts", headers=h, json={
            "name": "A", "type": "joint", "opening_balance": 0,
            "opening_date": "2026-01-01"}).json()["id"]
        body = {"account_id": aid, "date": "2026-01-02",
                "amount": -100, "payee": "X", "external_ref": "dup-1"}
        assert _client.post(
            "/api/v1/transactions", headers=h,
            json=body).status_code == 201
        r = _client.post("/api/v1/transactions",
                         headers=h, json=body)
        assert r.status_code == 409
        _problem(r.json(), 409)


# ---------- response conformance ---------------------------------

def _resolve(spec: dict, node: dict) -> dict:
    """Inline ``#/components/schemas/X`` refs (deep) for jsonschema."""
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        return _resolve(spec, spec["components"]["schemas"][name])
    out = dict(node)
    if "properties" in out:
        out["properties"] = {k: _resolve(spec, v)
                             for k, v in out["properties"].items()}
    for key in ("items",):
        if key in out and isinstance(out[key], dict):
            out[key] = _resolve(spec, out[key])
    for comb in ("anyOf", "oneOf", "allOf"):
        if comb in out:
            out[comb] = [_resolve(spec, s) for s in out[comb]]
    return out


class TestResponseConformance:
    """Round-trip: POST then GET and validate the payload against the
    spec's declared schema for that endpoint."""

    def _schema_for(self, path: str, method: str,
                    status: int = 200) -> dict:
        op = _app.openapi()["paths"][path][method]
        sch = op["responses"][str(status)]["content"][
            "application/json"]["schema"]
        return _resolve(_app.openapi(), sch)

    def test_household_get_conforms(self) -> None:
        page = _client.get("/api/v1/households").json()
        jsonschema.validate(
            page, self._schema_for("/api/v1/households", "get"))

    def test_account_get_conforms(self) -> None:
        hid = _client.post("/api/v1/households",
                           json={"name": "Conf"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        _client.post("/api/v1/accounts", headers=h, json={
            "name": "A", "type": "joint", "opening_balance": 0,
            "opening_date": "2026-01-01"})
        page = _client.get("/api/v1/accounts", headers=h).json()
        jsonschema.validate(
            page, self._schema_for("/api/v1/accounts", "get"))

    def test_transaction_post_conforms(self) -> None:
        hid = _client.post("/api/v1/households",
                           json={"name": "Tx"}).json()["id"]
        h = {"X-Household-Id": str(hid)}
        aid = _client.post("/api/v1/accounts", headers=h, json={
            "name": "A", "type": "joint", "opening_balance": 0,
            "opening_date": "2026-01-01"}).json()["id"]
        created = _client.post(
            "/api/v1/transactions", headers=h,
            json={"account_id": aid, "date": "2026-01-02",
                  "amount": -100, "payee": "X"}).json()
        jsonschema.validate(
            created, self._schema_for("/api/v1/transactions", "post",
                                      201))


# ---------- schemathesis sweep ------------------------------------

schema = schemathesis.openapi.from_asgi("/openapi.json", _app)


@schema.parametrize()
@settings(max_examples=10, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.data_too_large])
def test_no_server_errors(case: Any) -> None:
    """Every declared operation, fuzzed: the only hard gate is
    *no 5xx* — 4xx responses are acceptable contract behaviour and
    are pinned individually in TestErrorUniformity."""
    response = case.call()
    case.validate_response(
        response,
        checks=[schemathesis.checks.not_a_server_error])
