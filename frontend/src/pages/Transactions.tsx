/** Transaction review (issue #124) + global search/saved filters
 * (issue #126): payee search, status/category filters with named saved
 * sets, inline payee/category edit, bulk select + bulk-categorise,
 * status badges, windowed rows, the expected↔actual match-confirm
 * queue, and a reason-enforced correction flow for reconciled rows —
 * never a silent edit. */
import { useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Badge } from "../components/Badge";
import { Money } from "../components/Money";
import { formatDate } from "../lib/format";

type Txn = components["schemas"]["TransactionOut"];
type Category = components["schemas"]["CategoryOut"];
type Status = components["schemas"]["TransactionStatus"];
type Recon = components["schemas"]["ReconciliationOut"];

const STATUSES: Status[] = ["pending", "cleared", "reconciled", "void"];
const SAVED_KEY = "ff.txnFilters";
const ROW_PX = 46;
const OVERSCAN = 8;

interface Filters { q: string; status: string; category_id: string }

function loadSaved(): Record<string, Filters> {
  try {
    return JSON.parse(localStorage.getItem(SAVED_KEY) ?? "{}");
  } catch { return {}; }
}

/** Windowed row range for a scrollable container — renders only the
 * visible slice plus overscan, keeping large ledgers fast (#124). */
function useWindowed(count: number) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(600);
  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    setScrollTop(el.scrollTop);
    setHeight(el.clientHeight);
  };
  const first = Math.max(0, Math.floor(scrollTop / ROW_PX) - OVERSCAN);
  const last = Math.min(
    count, Math.ceil((scrollTop + height) / ROW_PX) + OVERSCAN);
  return { ref, onScroll, first, last };
}

export function Transactions() {
  const households = useHouseholds();
  const categories = useApi(
    () => api.get("/api/v1/categories"), households.data !== null);
  const [filters, setFilters] = useState<Filters>(
    { q: "", status: "", category_id: "" });
  const [saved, setSaved] = useState<Record<string, Filters>>(loadSaved);
  const [rev, setRev] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkCat, setBulkCat] = useState("");
  const [correcting, setCorrecting] = useState<Txn | null>(null);
  const state = useApi(
    () => api.get("/api/v1/transactions", {
      query: {
        q: filters.q || undefined,
        status: (filters.status || undefined) as Status | undefined,
        category_id: filters.category_id
          ? Number(filters.category_id) : undefined,
        size: 200,
      },
    }),
    households.data !== null,
    [filters, rev],
  );
  const queue = useApi(
    () => api.get("/api/v1/reconcile/queue"), households.data !== null,
    [rev]);
  const items = useMemo(() => state.data?.items ?? [], [state.data]);
  const win = useWindowed(items.length);
  const catName = useMemo(() => {
    const m = new Map<number, string>();
    for (const c of categories.data?.items ?? []) m.set(c.id, c.name);
    return m;
  }, [categories.data]);

  const patch = (id: number, body: object) =>
    api.patch("/api/v1/transactions/{txn_id}",
      { path: { txn_id: id }, body })
      .then(() => setRev((r) => r + 1));
  const bulk = () => {
    if (!bulkCat || selected.size === 0) return;
    void api.post("/api/v1/transactions/bulk", {
      body: { ids: [...selected], category_id: Number(bulkCat) },
    }).then(() => {
      setSelected(new Set());
      setRev((r) => r + 1);
    });
  };
  const reconAct = (rid: number, action: "confirm" | "dismiss") =>
    (action === "confirm"
      ? api.post("/api/v1/reconcile/{rid}/confirm", { path: { rid } })
      : api.post("/api/v1/reconcile/{rid}/dismiss", { path: { rid } })
    ).then(() => setRev((r) => r + 1));
  const saveFilter = () => {
    const name = window.prompt("Filter name");
    if (!name) return;
    const next = { ...saved, [name]: filters };
    setSaved(next);
    localStorage.setItem(SAVED_KEY, JSON.stringify(next));
  };
  const toggle = (id: number) => {
    const s = new Set(selected);
    if (s.has(id)) s.delete(id); else s.add(id);
    setSelected(s);
  };

  return (
    <section>
      <h1>Transactions</h1>
      <form
        onSubmit={(e) => e.preventDefault()}
        aria-label="Transaction filters"
      >
        <input
          type="search"
          placeholder="Search payee…"
          aria-label="Search payee"
          value={filters.q}
          onChange={(e) =>
            setFilters({ ...filters, q: e.target.value })}
        />
        <select
          aria-label="Status"
          value={filters.status}
          onChange={(e) =>
            setFilters({ ...filters, status: e.target.value })}
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select
          aria-label="Category"
          value={filters.category_id}
          onChange={(e) =>
            setFilters({ ...filters, category_id: e.target.value })}
        >
          <option value="">All categories</option>
          {(categories.data?.items ?? []).map((c: Category) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <select
          aria-label="Saved filters"
          value=""
          onChange={(e) => {
            const f = saved[e.target.value];
            if (f) setFilters(f);
          }}
        >
          <option value="">Saved filters…</option>
          {Object.keys(saved).map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
        <button type="button" onClick={saveFilter}>Save filter</button>
      </form>
      {selected.size > 0 && (
        <p>
          {selected.size} selected{" "}
          <select
            aria-label="Bulk category"
            value={bulkCat}
            onChange={(e) => setBulkCat(e.target.value)}
          >
            <option value="">Categorise as…</option>
            {(categories.data?.items ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <button onClick={bulk}>Apply to selected</button>
        </p>
      )}
      {(queue.data?.length ?? 0) > 0 && (
          <section aria-label="Reconciliation queue">
            <h2>Expected matches ({queue.data?.length ?? 0})</h2>
            <table className="ff-table">
              <thead>
                <tr>
                  <th>Expected</th>
                  <th>When</th>
                  <th className="ff-num">Expected</th>
                  <th className="ff-num">Drift</th>
                  <th>Match</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {queue.data?.map((r: Recon) => (
                  <tr key={r.id}>
                    <td>{r.expected_label ?? "—"}</td>
                    <td>{r.expected_date ? formatDate(r.expected_date) : "—"}</td>
                    <td className="ff-num">
                      {r.expected_amount_pence != null
                        ? <Money pence={r.expected_amount_pence} /> : "—"}
                    </td>
                    <td className="ff-num">
                      {r.amount_drift_pence != null
                        ? <Money pence={r.amount_drift_pence} /> : "—"}
                    </td>
                    <td><Badge tone="info">{r.score}%</Badge></td>
                    <td>
                      <button onClick={() => reconAct(r.id, "confirm")}>
                        Confirm
                      </button>{" "}
                      <button onClick={() => reconAct(r.id, "dismiss")}>
                        Dismiss
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
      )}
      <DataView
        state={{ ...state, data: state.data?.items ?? null }}
        empty="No transactions recorded."
      >
        {(rows) => (
          <div
            ref={win.ref}
            onScroll={win.onScroll}
            className="ff-table-window"
            role="region"
            aria-label="Transaction list"
            tabIndex={0}
          >
            <table className="ff-table">
              <thead>
                <tr>
                  <th aria-label="Select" />
                  <th>Date</th>
                  <th>Payee</th>
                  <th>Category</th>
                  <th>Status</th>
                  <th className="ff-num">Amount</th>
                </tr>
              </thead>
              <tbody>
                {win.first > 0 && (
                  <tr aria-hidden="true">
                    <td colSpan={6} style={{ height: win.first * ROW_PX, padding: 0, border: 0 }} />
                  </tr>
                )}
                {rows.slice(win.first, win.last).map((t: Txn) => {
                  const locked = t.status === "reconciled"
                    || t.status === "void";
                  return (
                    <tr key={t.id} style={{ height: ROW_PX }}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${t.payee} ${formatDate(t.date)}`}
                          checked={selected.has(t.id)}
                          onChange={() => toggle(t.id)}
                        />
                      </td>
                      <td>{formatDate(t.date)}</td>
                      <td>
                        {locked ? t.payee : (
                          <InlineText
                            value={t.payee}
                            label={`Edit payee for ${t.payee}`}
                            onSave={(v) => patch(t.id, { payee: v })}
                          />
                        )}
                      </td>
                      <td>
                        {locked ? (catName.get(t.category_id ?? -1) ?? "—") : (
                          <select
                            aria-label={`Category for ${t.payee}`}
                            value={t.category_id ?? ""}
                            onChange={(e) =>
                              void patch(t.id, {
                                category_id: e.target.value
                                  ? Number(e.target.value) : null,
                              })}
                          >
                            <option value="">—</option>
                            {(categories.data?.items ?? []).map((c) => (
                              <option key={c.id} value={c.id}>{c.name}</option>
                            ))}
                          </select>
                        )}
                      </td>
                      <td>
                        <Badge tone={
                          t.status === "reconciled" ? "success"
                            : t.status === "void" ? "error"
                            : t.status === "cleared" ? "info" : "neutral"
                        }>{t.status}</Badge>
                        {t.status === "reconciled" && (
                          <button
                            className="ff-inline-edit"
                            aria-label={`Correct ${t.payee}`}
                            onClick={() => setCorrecting(t)}
                          >
                            {" "}correct…
                          </button>
                        )}
                      </td>
                      <td className={`ff-num ${t.amount_pence < 0 ? "ff-neg" : "ff-pos"}`}>
                        <Money pence={t.amount_pence} />
                      </td>
                    </tr>
                  );
                })}
                {win.last < rows.length && (
                  <tr aria-hidden="true">
                    <td colSpan={6} style={{ height: (rows.length - win.last) * ROW_PX, padding: 0, border: 0 }} />
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </DataView>
      {correcting && (
        <CorrectionForm
          txn={correcting}
          categories={categories.data?.items ?? []}
          onDone={() => { setCorrecting(null); setRev((r) => r + 1); }}
          onCancel={() => setCorrecting(null)}
        />
      )}
    </section>
  );
}

/** Reason-enforced correction form for a reconciled transaction —
 * supersedes, never edits (issue #124). */
function CorrectionForm({ txn, categories, onDone, onCancel }: {
  txn: Txn;
  categories: Category[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [payee, setPayee] = useState(txn.payee);
  const [categoryId, setCategoryId] = useState(String(txn.category_id ?? ""));
  const [notes, setNotes] = useState(txn.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    api.post("/api/v1/transactions/{txn_id}/correct", {
      path: { txn_id: txn.id },
      body: {
        reason,
        payee: payee !== txn.payee ? payee : undefined,
        category_id: categoryId !== String(txn.category_id ?? "")
          ? (categoryId ? Number(categoryId) : null)
          : undefined,
        notes: notes !== (txn.notes ?? "") ? notes : undefined,
      },
    }).then(onDone).catch((err) => {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Correction failed");
    });
  };
  return (
    <form
      aria-label={`Correction for ${txn.payee}`}
      onSubmit={submit}
      className="ff-card"
    >
      <h2>Correct reconciled transaction</h2>
      <p className="ff-muted">
        The original is voided and a corrected entry re-enters review —
        history is never edited.
      </p>
      <label>
        Reason (required)
        <input
          required
          aria-label="Correction reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      <label>
        Payee
        <input
          aria-label="Corrected payee"
          value={payee}
          onChange={(e) => setPayee(e.target.value)}
        />
      </label>
      <label>
        Category
        <select
          aria-label="Corrected category"
          value={categoryId}
          onChange={(e) => setCategoryId(e.target.value)}
        >
          <option value="">—</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </label>
      <label>
        Notes
        <input
          aria-label="Corrected notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </label>
      {error && <p role="alert" className="ff-error">{error}</p>}
      <button type="submit" disabled={busy || !reason.trim()}>
        {busy ? "Correcting…" : "Issue correction"}
      </button>{" "}
      <button type="button" onClick={onCancel}>Cancel</button>
    </form>
  );
}

/** Click-to-edit inline text field (payee). */
function InlineText({ value, label, onSave }: {
  value: string;
  label: string;
  onSave: (v: string) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  if (!editing) {
    return (
      <button
        className="ff-inline-edit"
        aria-label={label}
        onClick={() => { setDraft(value); setEditing(true); }}
      >
        {value}
      </button>
    );
  }
  return (
    <input
      autoFocus
      aria-label={label}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        setEditing(false);
        if (draft !== value && draft.trim()) void onSave(draft.trim());
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") { setDraft(value); setEditing(false); }
      }}
    />
  );
}
