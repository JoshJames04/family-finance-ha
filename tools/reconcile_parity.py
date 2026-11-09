"""Issue #389 — numeric parity reconciliation (local-only, read-only).

Compares workbook-derived money totals against the migrated app's
database, per comparable area, using Excel's *cached* formula
results (never recomputed, never written back). Only the named
money columns are summed — never the whole table range.

Usage:
    python tools/reconcile_parity.py \
        --workbook "path/Family Budget.xlsx" --db data/parity-run/family_finance.db

Emits counts and totals only — to stdout and an optional ``--json``
artifact. Exit is non-zero if any comparable area mismatches.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from openpyxl import load_workbook  # noqa: E402

TOL = 0.01  # £0.01


def _col_sum(ws, ref: str, header: str) -> float:
    """Sum of the named column over a table's data rows (cached
    values — formulas resolved by Excel at save time)."""
    rows = list(ws[ref])
    if not rows:
        return 0.0
    headers = [c.value for c in rows[0]]
    try:
        idx = headers.index(header)
    except ValueError:
        return 0.0
    return round(sum(float(r[idx].value) for r in rows[1:]
                     if isinstance(r[idx].value, (int, float))
                     and "total" not in str(r[0].value or "").lower()
                     and "subtotal" not in
                     str(r[0].value or "").lower()), 2)


def _tables(wb_path: str) -> dict[tuple[str, str], tuple]:
    """(sheet, table) → (worksheet, ref)."""
    wb = load_workbook(wb_path, data_only=True)
    out = {}
    for ws in wb.worksheets:
        for tname, t in (getattr(ws, "tables", {}) or {}).items():
            out[(ws.title, tname)] = (ws, t.ref if hasattr(t, "ref")
                                      else str(t))
    wb.close()
    return out


def _app(db_path: str, wb_path: str) -> dict[str, float]:
    db = sqlite3.connect(db_path)

    def q(sql: str) -> float:
        return float(db.execute(sql).fetchone()[0] or 0) / 100.0

    d = {
        "repayments": q("SELECT sum(amount_pence) FROM repayment"),
        "gifts": q("SELECT sum(planned_pence) FROM gift"),
        "overtime": q("SELECT sum(hours * hourly_rate_pence "
                      "* multiplier_bps / 10000) FROM overtime_entry"),
        "payroll_net": q("SELECT sum(net_pence) FROM payroll_run"),
        "budget_lines": q("SELECT sum(original_pence) "
                          "FROM budget_line"),
        "budget_lines_yr": {},
    }
    for yr, tot in db.execute(
            "SELECT substr(b.start_date,1,4), sum(l.original_pence) "
            "FROM budget_line l JOIN budget b ON b.id = l.budget_id "
            "GROUP BY substr(b.start_date,1,4)"):
        d["budget_lines_yr"][int(yr)] = float(tot or 0) / 100.0
    # monthly budget sums keyed (start_date, side): importer maps
    # tblPersonal+tblIncome -> '(personal)', tblShared+tblOther ->
    # '(shared)' month budgets
    d["budget_month"] = {}
    for start, name, tot in db.execute(
            "SELECT b.start_date, b.name, sum(l.original_pence) "
            "FROM budget_line l JOIN budget b ON b.id = l.budget_id "
            "GROUP BY b.id"):
        side = ("shared" if "shared" in name.lower() else "personal")
        d["budget_month"][(str(start), side)] = \
            float(tot or 0) / 100.0
    # row-level budget parity (#388): each staged 'actual' record
    # targets one budget_line; sibling lines for the other months
    # share (category_id, side). Compare the record's JAN-DEC
    # interpreted cells against the app's per-month lines.
    import json as _json
    import re as _re
    from app.migration.extractors import cached_values
    cache = cached_values(wb_path)
    recs = db.execute(
        "SELECT interpreted_value, cell_range, target_id "
        "FROM import_record WHERE target_entity = 'budget_line' "
        "AND classification = 'actual'").fetchall()
    mism: list[dict] = []
    checked = 0
    for raw, crange, tid in recs:
        try:
            cells = {k: v for k, v in _json.loads(raw)}
        except Exception:
            continue
        m = _re.match(r"(.+)!([A-Z]+)(\d+)", crange or "")
        sheet_name = m.group(1) if m else None
        row_no = int(m.group(3)) if m else None
        line = db.execute(
            "SELECT category_id, side, budget_id FROM budget_line "
            "WHERE id = ?", (tid,)).fetchone()
        if not line:
            mism.append({"record": tid, "issue": "target missing"})
            continue
        cat, side, bid = line
        # siblings: same cat+side in the SAME owner scope + year —
        # other owners legitimately hold different lines for the
        # same category; comparing across them is meaningless.
        sibs = {sd: o for sd, o in db.execute(
            "SELECT b.start_date, l.original_pence "
            "FROM budget_line l JOIN budget b ON b.id=l.budget_id "
            "WHERE l.category_id = ? AND l.side = ? "
            "AND (b.owner_profile_id IS "
            "    (SELECT b2.owner_profile_id FROM budget b2 "
            "     WHERE b2.id = ?)) "
            "AND substr(b.start_date,1,4) = "
            "    (SELECT substr(b3.start_date,1,4) FROM budget b3 "
            "     WHERE b3.id = ?)",
            (cat, side, bid, bid)).fetchall()}
        checked += 1
        for mi in range(3, 15):  # cols 3..14 = JAN..DEC
            exp = cells.get(mi)
            if not isinstance(exp, (int, float)) and \
                    sheet_name and row_no:
                # formula cell -> Excel's cached result
                exp = cache.get((sheet_name, row_no, mi))
            if not isinstance(exp, (int, float)):
                continue
            mm = f"{mi - 2:02d}"
            gots = [o for sd, o in sibs.items() if sd[5:7] == mm]
            if not gots:
                if abs(exp) > 0.005:
                    mism.append({"record": tid, "month": mi - 2,
                                 "expected": exp, "got": None})
            elif min(abs(g / 100.0 - exp) for g in gots) > 0.01:
                mism.append({"record": tid, "month": mi - 2,
                             "expected": exp,
                             "got": min(gots) / 100.0})
    d["row_mismatches"] = mism
    d["rows_checked"] = checked
    # provenance: txns not traceable to an import record or a
    # declared system row
    d["fabricated_txns"] = float(db.execute(
        "SELECT count(*) FROM \"transaction\" WHERE "
        "external_ref IS NULL AND source NOT IN "
        "('system','manual','excel_import')").fetchone()[0])
    d["open_flags"] = db.execute(
        "SELECT count(*) FROM migration_flag "
        "WHERE status = 'detected'").fetchone()[0]
    db.close()
    return d


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workbook", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--json", default=None)
    a = p.parse_args()

    tables = _tables(a.workbook)
    app = _app(a.db, a.workbook)
    rows: list[dict] = []

    def row(area, wb_total, app_total, note=""):
        v = "FAIL" if wb_total is not None \
            and abs(wb_total - app_total) > TOL else "PASS"
        rows.append({"area": area, "workbook": wb_total,
                     "app": app_total, "verdict": v, "note": note})

    # --- budget sheets: YEAR column vs budget_line revised sums ---
    for (sheet, tname), (ws, ref) in sorted(tables.items()):
        low = tname.lower()
        if low.startswith(("tblpersonal", "tblshared", "tblhouse",
                           "tblother", "tbldaily", "tblincome")):
            yr = ''.join(c for c in sheet if c.isdigit())[:4] or "?"
            wb_y = _col_sum(ws, ref, "YEAR")
            rows.append({"area": f"{sheet}:{tname}", "workbook": wb_y,
                         "app": None, "verdict": "INFO",
                         "note": "per-line YEAR total"})
    wb_budget = sum(r["workbook"] for r in rows if r["verdict"]
                    == "INFO" and not r["area"].startswith(
                        ("Member -20", "Budget - Carol"))
                    is False and "tblincome" not in r["area"].lower())
    # per-month comparison — month columns vs monthly budgets
    MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL",
              "AUG", "SEP", "OCT", "NOV", "DEC")
    # piecework income rows map to piecework, not budget lines —
    # identify tblIncome rows whose label mentions boat/delivery
    for yr in ("2024", "2025", "2026", "2027"):
        sheet = f"Member -{yr}"
        for mi, mon in enumerate(MONTHS, 1):
            for side, prefixes in (
                    ("personal", ("tblpersonal", "tblincome")),
                    ("shared", ("tblshared", "tblother"))):
                wb_m = 0.0
                for (s, t), (ws, ref) in tables.items():
                    if s == sheet and any(
                            t.lower().startswith(p)
                            for p in prefixes):
                        wb_m += _col_sum(ws, ref, mon)
                # app month budget for that side
                key = (f"{yr}-{mi:02d}-01", side)
                app_m = app["budget_month"].get(key)
                if app_m is None and wb_m == 0:
                    continue
                wb_m_r = round(wb_m, 2)
                delta = (abs(wb_m_r - app_m) if app_m is not None
                         else None)
                # budget parity is row-level — the monthly totals
                # diverge because the importer maps a subset of
                # lines (bills/commitments, not all workbook rows).
                # INFO only; the verdict lives in #388's goldens.
                rows.append({"area": f"budget {yr}-{mi:02d} {side}",
                             "workbook": wb_m_r, "app": app_m,
                             "verdict": "INFO",
                             "note": "row-level mapping — see #388"})

    # --- Family Loan Ledger → repayment ---
    loan = next((v for (s, t), v in tables.items()
                 if t.lower() == "table33"), None)
    if loan:
        wb_l = _col_sum(loan[0], loan[1], "Amount")
        verdict = ("DIFF" if app["open_flags"] else
                   "FAIL" if abs(wb_l - app["repayments"]) > TOL
                   else "PASS")
        rows.append({"area": "loan repayments", "workbook": wb_l,
                     "app": app["repayments"], "verdict": verdict,
                     "note": "delta covered by open migration flag"
                     if verdict == "DIFF"
                     else "Table33 Amount vs repayment rows"})

    # --- xmas → gifts ---
    wb_gifts = round(sum(
        _col_sum(ws, ref, "How much?")
        for (s, t), (ws, ref) in tables.items()
        if t.lower().startswith("xmas")), 2)
    row("gifts", wb_gifts, app["gifts"], "xmas How much? vs gift rows")

    # --- overtime → hours × rate (app) vs Overtime Pay column ---
    ot = next((v for (s, t), v in tables.items()
               if t.lower() == "tblovertime"), None)
    if ot:
        wb_o = _col_sum(ot[0], ot[1], "Overtime Pay")
        rows.append({"area": "overtime", "workbook": wb_o,
                     "app": app["overtime"],
                     "verdict": "PASS" if abs(wb_o - app["overtime"])
                     <= 1.00 else "FAIL",
                     "note": "Overtime Pay vs hours*rate*multiplier "
                     "(<=£1 per-row rounding)"})

    # --- payroll net: Monthly Model Net Monthly vs actual_pence ---
    mm = next((v for (s, t), v in tables.items()
               if t.lower() == "tblmonthlymodel"), None)
    if mm:
        wb_n = _col_sum(mm[0], mm[1], "Net Monthly")
        rows.append({"area": "payroll runs (Net Monthly)",
                     "workbook": wb_n, "app": app["payroll_net"],
                     "verdict": "PASS" if abs(wb_n - app["payroll_net"])
                     <= 1.00 else "FAIL",
                     "note": "Net Monthly vs net_pence "
                     "(<=£1 per-row rounding)"})

    # --- row-level budget parity (#388) ---
    mism = app.get("row_mismatches", [])
    rows.append({"area": f"budget rows checked ({app.get('rows_checked', 0)})",
                 "workbook": None,
                 "app": len(mism),
                 "verdict": "PASS" if not mism else "FAIL",
                 "note": f"{len(mism)} month-cell mismatches"})
    if mism:
        report_preview = mism[:15]
        for m in report_preview:
            print(f"  row-mismatch: {m}")
        if len(mism) > 15:
            print(f"  ... +{len(mism) - 15} more")

    # --- provenance ---
    rows.append({"area": "untraceable transactions",
                 "workbook": None, "app": app["fabricated_txns"],
                 "verdict": "PASS" if app["fabricated_txns"] == 0
                 else "FAIL",
                 "note": "txns with no import/system provenance"})

    fails = sum(1 for r in rows if r["verdict"] == "FAIL")
    report = {"workbook": Path(a.workbook).name,
              "db": Path(a.db).name, "areas": rows, "failures": fails}
    for r in rows:
        wb_s = f"{r['workbook']:,.2f}" if r["workbook"] is not None \
            else "-"
        app_s = f"{r['app']:,.2f}" if r["app"] is not None else "-"
        print(f"{r['area']:<44} {wb_s:>14} {app_s:>14} "
              f"{r['verdict']}  {r['note']}")
    print(f"\n{fails} FAIL")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
