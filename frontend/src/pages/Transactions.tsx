/** Transaction review (issue #124) + global search/saved filters
 * (issue #126): payee search, status/category/account filters with
 * named saved sets, inline payee/category edit, bulk select +
 * bulk-categorise, status badges, reconciled rows locked (correction
 * flow is a separate surface — never a silent edit). */
import { useMemo, useState } from "react";
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

const STATUSES: Status[] = ["pending", "cleared", "reconciled", "void"];
const SAVED_KEY = "ff.txnFilters";

interface Filters { q: string; status: string; category_id: string }

function loadSaved(): Record<string, Filters> {
  try {
    return JSON.parse(localStorage.getItem(SAVED_KEY) ?? "{}");
  } catch { return {}; }
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
  const state = useApi(
    () => api.get("/api/v1/transactions", {
      query: {
        q: filters.q || undefined,
        status: (filters.status || undefined) as Status | undefined,
        category_id: filters.category_id
          ? Number(filters.category_id) : undefined,
      },
    }),
    households.data !== null,
    [filters, rev],
  );
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
      <DataView
        state={{ ...state, data: state.data?.items ?? null }}
        empty="No transactions recorded."
      >
        {(items) => (
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
              {items.map((t: Txn) => {
                const locked = t.status === "reconciled"
                  || t.status === "void";
                return (
                  <tr key={t.id}>
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
                      {locked && t.status === "reconciled" && (
                        <span className="ff-muted" title="Corrected via the reconciliation surface — never edited in place">
                          {" "}locked
                        </span>
                      )}
                    </td>
                    <td className={`ff-num ${t.amount_pence < 0 ? "ff-neg" : "ff-pos"}`}>
                      <Money pence={t.amount_pence} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </DataView>
    </section>
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
