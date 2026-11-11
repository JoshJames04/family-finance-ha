"""Ingress-emulating dev proxy (issue #460 test harness).

Stands in for the Home Assistant Supervisor ingress path so the
production image can be exercised end-to-end through the same proxy
contract as a real add-on install:

- Mounted under /api/hassio_ingress/<token>/ like real ingress; the
  prefix is stripped before forwarding, as the Supervisor does.
- X-Remote-User-* headers are injected (and any inbound copies are
  stripped first — spoof attempts must never reach the app).
- Runs at a static IP on the compose network so the backend's
  supervisor-IP trust check sees the peer HA would present.
- /__chaos endpoints inject upstream failures (503/504/delay) per
  path pattern — deterministic transient-failure testing without
  touching the application.

Development only — never deployed.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response

UPSTREAM = os.environ.get("UPSTREAM", "http://backend:8099")
INGRESS_RE = re.compile(r"^/api/hassio_ingress/(?P<token>[^/]+)(?P<rest>/.*)?$")
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding",
              "te", "trailer", "upgrade", "content-length", "host"}
REMOTE_HEADERS = {
    "x-remote-user-id": "ha-user-1",
    "x-remote-user-name": "josh",
    "x-remote-user-display-name": "Josh",
}

app = FastAPI(title="ingress-proxy")
_client = httpx.AsyncClient(timeout=httpx.Timeout(90.0))


class ChaosRule:
    def __init__(self, match: str, status: int | None = None,
                 delay_ms: int = 0, times: int = -1) -> None:
        self.match = re.compile(match)
        self.status = status
        self.delay_ms = delay_ms
        self.times = times   # -1 = forever
        self.hits = 0


_rules: list[ChaosRule] = []
_stats: dict[str, Any] = {"requests": 0, "injected": 0, "by_status": {}}


@app.post("/__chaos/rules")
async def set_rules(rules: list[dict[str, Any]]) -> dict[str, int]:
    """Replace all chaos rules. Each: {match, status?, delay_ms?, times?}."""
    _rules.clear()
    for r in rules:
        _rules.append(ChaosRule(r["match"], r.get("status"),
                                int(r.get("delay_ms", 0)),
                                int(r.get("times", -1))))
    return {"rules": len(_rules)}


@app.delete("/__chaos/rules")
async def clear_rules() -> dict[str, int]:
    n = len(_rules)
    _rules.clear()
    return {"cleared": n}


@app.get("/__chaos/stats")
async def chaos_stats() -> dict[str, Any]:
    return {**_stats,
            "rules": [{"match": r.match.pattern, "hits": r.hits,
                       "remaining": r.times} for r in _rules]}


@app.api_route("/{full_path:path}",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def proxy(request: Request, full_path: str) -> Response:
    raw = "/" + full_path
    m = INGRESS_RE.match(raw)
    path = (m.group("rest") or "/") if m else raw
    if request.url.query:
        path += "?" + request.url.query

    for rule in list(_rules):
        if rule.times == 0 or not rule.match.search(path):
            continue
        rule.hits += 1
        _stats["injected"] += 1
        if rule.times > 0:
            rule.times -= 1
        if rule.delay_ms:
            import asyncio
            await asyncio.sleep(rule.delay_ms / 1000)
        if rule.status is not None:
            _stats["requests"] += 1
            _stats["by_status"][rule.status] = (
                _stats["by_status"].get(rule.status, 0) + 1)
            return Response(f"chaos-injected {rule.status}".encode(),
                            status_code=rule.status)

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP
        and not k.lower().startswith("x-remote-user")
        and k.lower() != "x-ingress-path"}
    headers.update(REMOTE_HEADERS)
    token = m.group("token") if m else "direct"
    headers["x-ingress-path"] = f"/api/hassio_ingress/{token}"

    started = time.monotonic()
    try:
        upstream = await _client.request(
            request.method, f"{UPSTREAM}{path}",
            headers=headers,
            content=await request.body())
    except httpx.RequestError as exc:
        _stats["requests"] += 1
        _stats["by_status"][503] = _stats["by_status"].get(503, 0) + 1
        return Response(f"upstream error: {exc}".encode(),
                        status_code=503)
    _stats["requests"] += 1
    _stats["by_status"][upstream.status_code] = (
        _stats["by_status"].get(upstream.status_code, 0) + 1)
    _stats["last_ms"] = round((time.monotonic() - started) * 1000, 1)
    return Response(
        upstream.content, status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items()
                 if k.lower() not in HOP_BY_HOP},
        media_type=upstream.headers.get("content-type"))
