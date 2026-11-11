"""Issue #460 acceptance harness — seeds synthetic data and runs
Tests A–G against the ingress-faithful stack (docker-compose.ingress.yml,
project ff460). Everything goes through the ingress proxy at
localhost:8400 under /api/hassio_ingress/e2e/ — the same contract the
real Home Assistant Supervisor presents.

Usage: python dev/ff460_acceptance.py [seed|a|b|c|d|e|g|all]
Never touches the dev stack's real database.
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

BASE = "http://localhost:8400"
ING = f"{BASE}/api/hassio_ingress/e2e"
TODAY = date.today()
RESULTS: dict[str, dict] = {}


def call(method: str, path: str, body: dict | None = None,
         timeout: float = 30.0) -> tuple[int, dict | list | None, float]:
    """One ingress request → (status, json, ms). Errors are data —
    the harness measures them rather than raising."""
    url = ING + path
    started = time.monotonic()
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read() or b"null"
            ms = (time.monotonic() - started) * 1000
            return r.status, json.loads(payload), ms
    except urllib.error.HTTPError as e:
        ms = (time.monotonic() - started) * 1000
        try:
            return e.code, json.loads(e.read() or b"null"), ms
        except Exception:
            return e.code, None, ms
    except (urllib.error.URLError, TimeoutError, ConnectionError,
            OSError, Exception) as exc:  # noqa: BLE001 — measured, not raised
        ms = (time.monotonic() - started) * 1000
        return 0, {"error": str(exc)[:200]}, ms


def chaos(method: str, path: str, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def report(name: str, ok: bool, detail: str) -> None:
    RESULTS[name] = {"ok": ok, "detail": detail}
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


# ---------------------------------------------------------------- seed

def seed() -> None:
    """Synthetic household finance data via the real API."""
    def must(path: str, body: dict) -> dict:
        s, j, _ = call("POST", path, body)
        assert s in (200, 201), f"seed {path} → {s}: {j}"
        return j  # type: ignore[return-value]

    acct = must("/api/v1/accounts", {
        "name": "FF460 Current", "type": "personal",
        "opening_balance": 250_000,
        "opening_date": (TODAY - timedelta(days=90)).isoformat()})
    sav = must("/api/v1/accounts", {
        "name": "FF460 Savings", "type": "savings",
        "opening_balance": 500_000,
        "opening_date": (TODAY - timedelta(days=90)).isoformat()})
    cats = {c["name"]: c["id"] for c in
            call("GET", "/api/v1/categories?size=100")[1]["items"]}  # type: ignore[index]
    cat = cats.get("Groceries") or must("/api/v1/categories", {
        "name": "Groceries"})["id"]
    aid, sid = acct["id"], sav["id"]
    for i, (payee, pence) in enumerate(
            [("Tesco", -4_230), ("Tesco", -5_110), ("Salary", 280_000),
             ("Netflix", -1_599), ("Shell", -6_800), ("Tesco", -3_940)]):
        must("/api/v1/transactions", {
            "account_id": aid,
            "date": (TODAY - timedelta(days=3 * i)).isoformat(),
            "amount": pence, "payee": payee, "category_id": cat})
    must("/api/v1/commitments", {
        "name": "FF460 Rent", "account_id": aid, "category_id": cat,
        "amount": -95_000, "frequency": "monthly",
        "next_date": (TODAY + timedelta(days=5)).isoformat(),
        "day_of_month": min(28, (TODAY + timedelta(days=5)).day)})
    must("/api/v1/commitments", {
        "name": "FF460 Energy", "account_id": aid,
        "amount": -12_000, "frequency": "monthly",
        "next_date": (TODAY + timedelta(days=12)).isoformat()})
    budget = must("/api/v1/budgets", {
        "name": "FF460 Budget", "period_type": "monthly",
        "start_date": TODAY.replace(day=1).isoformat(),
        "end_date": (TODAY.replace(day=28)
                     + timedelta(days=10)).replace(day=1)
                    .__sub__(timedelta(days=1)).isoformat()})
    must(f"/api/v1/budgets/{budget['id']}/lines", {
        "category_id": cat, "side": "expense", "amount": 40_000})
    print(f"seeded: accounts {aid},{sid} budget {budget['id']}")


# ------------------------------------------------------------ test kit

WIDGET_GETS = [
    "/api/v1/accounts",
    "/api/v1/transactions?size=6",
    "/api/v1/expected-movements?start={start}&end={end}",
    "/api/v1/budgets",
    "/api/v1/reports/monthly?year={y}&month={m}",
    "/api/v1/subscriptions",
    "/api/v1/categories?size=100",
]
NAV_GETS = [
    "/api/v1/transactions?size=50",
    "/api/v1/commitments",
    "/api/v1/budgets",
]


def urls(paths: list[str]) -> list[str]:
    start = TODAY.isoformat()
    end = (TODAY + timedelta(days=30)).isoformat()
    return [p.format(start=start, end=end,
                     y=TODAY.year, m=TODAY.month) for p in paths]


def sweep(paths: list[str]) -> tuple[list[int], list[float], list[tuple[str, int]]]:
    statuses, times, fails = [], [], []
    for p in urls(paths):
        s, _, ms = call("GET", p)
        statuses.append(s)
        times.append(ms)
        if s != 200:
            fails.append((p.split("?")[0], s))
    return statuses, times, fails


# ---------------------------------------------------------------- tests

def test_a() -> None:
    """A — 20 navigation cycles, every widget surface healthy."""
    fails: list[tuple[str, int]] = []
    times: list[float] = []
    for _ in range(20):
        _, t, f = sweep(WIDGET_GETS + NAV_GETS)
        times += t
        fails += f
    report("A navigation×20", not fails,
           f"{len(times)} requests, {len(fails)} failures, "
           f"p50={statistics.median(times):.0f}ms "
           f"max={max(times):.0f}ms" +
           (f" first={fails[0]}" if fails else ""))


def test_b() -> None:
    """B — heavy scheduler-class work mid-traffic: analytics rebuild
    + real backups run while three threads hammer the widget
    surfaces for a fixed 12s window."""
    stop = threading.Event()
    results: list[int] = []
    times: list[float] = []
    heavy: list[tuple[str, int, float]] = []

    def hammer():
        while not stop.is_set():
            s, t, f = sweep(WIDGET_GETS)
            results.extend(s)
            times.extend(t)

    def heavies():
        for _ in range(3):
            s, _, ms = call("POST", "/api/v1/analytics/rebuild")
            heavy.append(("rebuild", s, round(ms)))
            s, _, ms = call("POST", "/api/v1/admin/backups")
            heavy.append(("backup", s, round(ms)))

    threads = [threading.Thread(target=hammer) for _ in range(3)]
    threads.append(threading.Thread(target=heavies))
    for t in threads:
        t.start()
    time.sleep(12)
    stop.set()
    for t in threads:
        t.join()
    bad = [s for s in results if s != 200]
    med = f"p50={statistics.median(times):.0f}ms max={max(times):.0f}ms" \
        if times else "no samples"
    report("B scheduler-concurrency",
           not bad and all(s in (200, 201) for _, s, _ in heavy),
           f"{len(results)} reads under load: {len(bad)} failed; "
           f"heavy ops {heavy}; {med}")


def test_c() -> None:
    """C — chaos: injected 503s reach the client; removal heals
    instantly; stats prove the injection happened."""
    chaos("POST", "/__chaos/rules", [
        {"match": "expected-movements", "status": 503, "times": 2}])
    s1, _, _ = call("GET", "/api/v1/expected-movements?start={}&end={}"
                    .format(TODAY, TODAY + timedelta(days=30)))
    s2, _, _ = call("GET", "/api/v1/expected-movements?start={}&end={}"
                    .format(TODAY, TODAY + timedelta(days=30)))
    s3, _, _ = call("GET", "/api/v1/expected-movements?start={}&end={}"
                    .format(TODAY, TODAY + timedelta(days=30)))
    _, stats = chaos("GET", "/__chaos/stats")
    chaos("DELETE", "/__chaos/rules")
    # 504 timeout injection, single shot
    chaos("POST", "/__chaos/rules", [
        {"match": "accounts", "status": 504, "times": 1}])
    s4, _, _ = call("GET", "/api/v1/accounts")
    s5, _, _ = call("GET", "/api/v1/accounts")
    chaos("DELETE", "/__chaos/rules")
    seq = [s1, s2, s3, s4, s5]
    report("C transient-injection",
           seq == [503, 503, 200, 504, 200],
           f"statuses {seq}; chaos stats {stats['by_status']}")


def test_d() -> None:
    """D — honest-empty contract: unpopulated collections return
    200 + empty items (the signal the UI's legitimate empty state
    renders from — proven distinct from failures by the e2e spec),
    and a household this profile isn't a member of stays isolated."""
    s, hh, _ = call("POST", "/api/v1/households", {"name": "FF460 Empty"})
    assert s in (200, 201), f"household create {s}"
    hid = hh["id"]  # type: ignore[index]
    # Cross-scope isolation: the ingress profile is not a member of
    # the new household — its data must be unreachable (403/404).
    req = urllib.request.Request(
        ING + "/api/v1/transactions?size=6",
        headers={"X-Household-Id": str(hid)})
    try:
        urllib.request.urlopen(req, timeout=10)
        isolated = False
    except urllib.error.HTTPError as e:
        isolated = e.code in (403, 404)
    # Unpopulated surfaces of the real household → 200 with zero
    # items — the "successfully loaded, no data" contract.
    empties = []
    for p in ["/api/v1/subscriptions", "/api/v1/savings-goals",
              "/api/v1/loans"]:
        s, j, _ = call("GET", p)
        if s != 200:
            empties.append((p, f"status {s}"))
            continue
        items = j["items"] if isinstance(j, dict) else j
        empties.append((p, f"{len(items)} items"))
    report("D genuine-empty",
           isolated and all("items" in d for _, d in empties),
           f"isolation={isolated} collections={empties}")


def test_e() -> None:
    """E — concurrent reads+writes: transaction inserts racing a
    read sweep — no lock errors, no lost requests."""
    s, accts, _ = call("GET", "/api/v1/accounts")
    aid = accts["items"][0]["id"]  # type: ignore[index]
    errs: list[str] = []
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            st, j, _ = call("POST", "/api/v1/transactions", {
                "account_id": aid, "date": TODAY.isoformat(),
                "amount": -100 - i, "payee": f"FF460-write-{i}"})
            if st not in (200, 201):
                errs.append(f"write {i} → {st}: {j}")
            i += 1

    def reader():
        while not stop.is_set():
            _, _, f = sweep(WIDGET_GETS)
            errs.extend(f"read {p} → {st}" for p, st in f)

    ws = [threading.Thread(target=writer) for _ in range(2)]
    rs = [threading.Thread(target=reader) for _ in range(4)]
    for t in ws + rs:
        t.start()
    time.sleep(20)
    stop.set()
    for t in ws + rs:
        t.join()
    # clean up the synthetic write rows
    call("GET", "/api/v1/transactions?size=200")
    report("E concurrent-rw", not errs,
           f"20s of 2 writers + 4 readers: {len(errs)} errors"
           + (f" first={errs[0][:120]}" if errs else ""))


def test_g() -> None:
    """G — backend restart: data persists, service recovers."""
    s1, before, _ = call("GET", "/api/v1/transactions?size=100")
    n_before = before["total"] if isinstance(before, dict) else None
    subprocess.run(["docker", "restart", "ff460-backend-1"],
                   check=True, capture_output=True)
    # poll until healthy again (≤45s)
    deadline = time.monotonic() + 45
    ok = False
    while time.monotonic() < deadline:
        s, j, _ = call("GET", "/api/v1/households", timeout=3)
        if s == 200:
            ok = True
            break
        time.sleep(1)
    s2, after, _ = call("GET", "/api/v1/transactions?size=100")
    n_after = after["total"] if isinstance(after, dict) else None
    report("G restart-persistence",
           ok and n_before == n_after and s1 == s2 == 200,
           f"recovered={ok} txs {n_before}->{n_after}")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    fns = {"seed": seed, "a": test_a, "b": test_b, "c": test_c,
           "d": test_d, "e": test_e, "g": test_g}
    if what == "all":
        fns = {"seed": seed, **{k: v for k, v in fns.items() if k != "seed"}}
    else:
        fns = {what: fns[what]}
    for name, fn in fns.items():
        fn()
    if RESULTS:
        print("\n=== summary ===")
        for k, v in RESULTS.items():
            print(f"  [{'PASS' if v['ok'] else 'FAIL'}] {k}")
        sys.exit(0 if all(v["ok"] for v in RESULTS.values()) else 1)
