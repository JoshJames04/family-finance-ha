/** Budget tables and forecast view (issue #125): original / revised /
 * actual / variance columns with accuracy indicators, reason-enforced
 * revision editing, visible lock state, and an expected-movements
 * side panel for the selected period — the money picture at a glance,
 * not a spreadsheet wall. */
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useApi, useHouseholds } from "../api/useApi";
import type { components } from "../api/schema";
import { DataView } from "../components/DataView";
import { Badge } from "../components/Badge";
import { Money } from "../components/Money";
import { formatDate } from "../lib/format";

type BudgetOut = components["schemas"]["BudgetOut"];
type Detail = components["schemas"]["BudgetDetail"];
type Line = components["schemas"]["BudgetLineOut"];
type Actuals = components["schemas"]["BudgetActuals"];
type Movement = components["schemas"]["ExpectedMovementOut"];
type Category = components["schemas"]["CategoryOut"];

interface LineAnalysis {
  original_pence: number;
  revised_pence: number | null;
  revision_reason: string | null;
  effective_pence: number;
  actual_pence: number;
  variance_pence: number;
  accuracy_pct: number;
  original_accuracy_pct: number;
}

export function Budget() {
  const households = useHouseholds();
  const budgets = useApi(
    () => api.get("/api/v1/budgets"), households.data !== null);
  const categories = useApi(
    () => api.get("/api/v1/categories"), households.data !== null);
  const [budgetId, setBudgetId] = useState<number | null>(null);
  const [rev, setRev] = useState(0);
  const [revising, setRevising] = useState<Line | null>(null);
  const id = budgetId ?? budgets.data?.items[0]?.id ?? null;
  const detail = useApi(
    () => api.get("/api/v1/budgets/{budget_id}", { path: { budget_id: id! } }),
    id !== null, [id, rev]);
  const actuals = useApi(
    () => api.get("/api/v1/budgets/{budget_id}/actuals",
      { path: { budget_id: id! } }),
    id !== null, [id, rev]);
  const period = detail.data
    ? { from: detail.data.start_date, to: detail.data.end_date }
    : null;
  const movements = useApi(
    () => api.get("/api/v1/expected-movements",
      { query: { start: period!.from, end: period!.to } }),
    period !== null, [period?.from, period?.to]);
  const catName = useMemo(() => {
    const m = new Map<number, string>();
    for (const c of categories.data?.items ?? []) m.set(c.id, c.name);
    return m;
  }, [categories.data]);
  const locked = detail.data
    ? detail.data.status === "locked" || detail.data.read_only
    : false;

  const sides: Array<{ key: "income" | "expense"; label: string }> = [
    { key: "income", label: "Income" },
    { key: "expense", label: "Expenses" },
  ];

  return (
    <section>
      <h1>Budget</h1>
      <DataView
        state={{ ...budgets, data: budgets.data?.items ?? null }}
        empty="No budgets created."
      >
        {(items) => (
          <>
            <label>
              Budget{" "}
              <select
                aria-label="Budget"
                value={id ?? ""}
                onChange={(e) => setBudgetId(Number(e.target.value))}
              >
                {items.map((b: BudgetOut) => (
                  <option key={b.id} value={b.id}>{b.name}</option>
                ))}
              </select>
            </label>
            <DataView state={detail} empty="">
              {(d: Detail) => (
                <div className="ff-budget-grid">
                  <div>
                    <h2>
                      {d.name}{" "}
                      <Badge tone={d.status === "locked" ? "neutral"
                        : d.status === "approved" ? "success" : "info"}>
                        {d.status}
                      </Badge>
                      {locked && (
                        <span className="ff-muted">
                          {" "}locked — read-only
                        </span>
                      )}
                    </h2>
                    <p className="ff-muted">
                      {formatDate(d.start_date)} → {formatDate(d.end_date)}
                    </p>
                    <DataView state={actuals} empty="">
                      {(a: Actuals) => (
                        <>
                          {(() => {
                            const analyses = a.lines as unknown as
                              Record<string, LineAnalysis>;
                            return sides.map((side) => {
                            const rows = d.lines.filter(
                              (l) => l.side === side.key);
                            if (rows.length === 0) return null;
                            return (
                              <table
                                className="ff-table ff-budget-table"
                                key={side.key}
                                aria-label={`${side.label} lines`}
                              >
                                <thead>
                                  <tr>
                                    <th colSpan={6}>{side.label}</th>
                                  </tr>
                                  <tr>
                                    <th>Category</th>
                                    <th className="ff-num">Original</th>
                                    <th className="ff-num">Revised</th>
                                    <th className="ff-num">Actual</th>
                                    <th className="ff-num">Variance</th>
                                    <th className="ff-num">Accuracy</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {rows.map((l: Line) => {
                                    const an = analyses[String(l.id)];
                                    const v = an?.variance_pence ?? 0;
                                    const acc = an?.accuracy_pct ?? null;
                                    return (
                                      <tr key={l.id}>
                                        <td data-label="Category">
                                          {catName.get(l.category_id)
                                            ?? `Category ${l.category_id}`}
                                          {an?.revision_reason && (
                                            <div className="ff-muted ff-reason">
                                              revised: {an.revision_reason}
                                            </div>
                                          )}
                                        </td>
                                        <td className="ff-num" data-label="Original">
                                          <Money pence={l.original_pence} />
                                        </td>
                                        <td className="ff-num" data-label="Revised">
                                          {locked ? (
                                            l.revised_pence != null
                                              ? <Money pence={l.revised_pence} />
                                              : "—"
                                          ) : (
                                            <button
                                              className="ff-inline-edit"
                                              aria-label={`Revise ${catName.get(l.category_id) ?? l.category_id}`}
                                              onClick={() => setRevising(l)}
                                            >
                                              {l.revised_pence != null
                                                ? <Money pence={l.revised_pence} />
                                                : "revise…"}
                                            </button>
                                          )}
                                        </td>
                                        <td className="ff-num" data-label="Actual">
                                          {an != null
                                            ? <Money pence={an.actual_pence} />
                                            : "—"}
                                        </td>
                                        <td
                                          className={`ff-num ${v < 0 ? "ff-neg" : "ff-pos"}`}
                                          data-label="Variance"
                                        >
                                          {an != null
                                            ? <Money pence={v} /> : "—"}
                                        </td>
                                        <td className="ff-num" data-label="Accuracy">
                                          {acc != null ? `${acc}%` : "—"}
                                        </td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            );
                          });
                          })()}
                        </>
                      )}
                    </DataView>
                  </div>
                  <aside aria-label="Upcoming in this period">
                    <h2>Expected this period</h2>
                    <DataView
                      state={{
                        ...movements,
                        data: movements.data ?? null,
                      }}
                      empty="Nothing expected in this period."
                    >
                      {(mvts: Movement[]) => (
                        <ul className="ff-list">
                          {mvts.slice(0, 12).map((m) => (
                            <li key={m.id}>
                              <span>{m.label}</span>
                              <span className="ff-muted">
                                {formatDate(m.expected_date)}
                              </span>
                              <span className={`ff-num ${m.amount_pence < 0 ? "ff-neg" : "ff-pos"}`}>
                                <Money pence={m.amount_pence} />
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </DataView>
                  </aside>
                </div>
              )}
            </DataView>
          </>
        )}
      </DataView>
      {revising && id !== null && (
        <ReviseForm
          line={revising}
          budgetId={id}
          locked={locked}
          onDone={() => { setRevising(null); setRev((r) => r + 1); }}
          onCancel={() => setRevising(null)}
        />
      )}
    </section>
  );
}

/** Revision form — the API requires a reason; the UI enforces it the
 * same way (issue #125). Amount entered in pounds, sent as Money. */
function ReviseForm({ line, budgetId, onDone, onCancel }: {
  line: Line;
  budgetId: number;
  locked: boolean;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [pounds, setPounds] = useState(
    line.revised_pence != null
      ? (line.revised_pence / 100).toFixed(2)
      : (line.original_pence / 100).toFixed(2));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    api.patch("/api/v1/budgets/{budget_id}/lines/{line_id}", {
      path: { budget_id: budgetId, line_id: line.id },
      body: { amount: pounds, reason },
    }).then(onDone).catch((err) => {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Revision failed");
    });
  };
  return (
    <form
      className="ff-card"
      aria-label="Revise budget line"
      onSubmit={submit}
    >
      <h2>Revise line</h2>
      <label>
        New amount (£)
        <input
          required
          type="number"
          step="0.01"
          min="0"
          aria-label="Revised amount"
          value={pounds}
          onChange={(e) => setPounds(e.target.value)}
        />
      </label>
      <label>
        Reason (required)
        <input
          required
          aria-label="Revision reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {error && <p role="alert" className="ff-error">{error}</p>}
      <button type="submit" disabled={busy || !reason.trim()}>
        {busy ? "Saving…" : "Save revision"}
      </button>{" "}
      <button type="button" onClick={onCancel}>Cancel</button>
    </form>
  );
}
